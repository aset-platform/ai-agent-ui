"""2-state Gaussian HMM stress-probability overlay.

Anti-lookahead invariant: ``stress_prob(window)`` ALWAYS calls
``predict_proba(window)`` on the trailing window only — never on
the full sample. The Viterbi smoothing path used by
``hmm.predict(X)`` over a full sequence uses future observations
to refine intermediate states; it MUST NOT be used for online
inference. Test ``test_filtered_no_lookahead`` is a hard CI gate.

Persistence: state stored in ``stocks.regime_hmm_state`` (one row
per monthly refit; warm-start from last persisted transmat).
"""
from __future__ import annotations

import logging
from datetime import date

import numpy as np

from backend.algo.regime.repo import (
    HmmStateRow,
    get_latest_hmm_state,
    upsert_hmm_state,
)

_logger = logging.getLogger(__name__)

N_STATES = 2
COVARIANCE_TYPE = "diag"
N_ITER_FIT = 200
RANDOM_STATE = 42


class StressHMM:
    """2-state Gaussian HMM. Features: (log_return,
    realized_vol_20d).

    State labels are stable: index 0 = lower vol (calm),
    index 1 = higher vol (stressed). Stress prob = posterior of
    state 1 on the last bar of a forward-only filtered window.
    """

    def __init__(self) -> None:
        self._model = None
        self.transmat: list[list[float]] | None = None
        self.means: list[list[float]] | None = None
        self.covars: list[list[list[float]]] | None = None
        self.trained_through: date | None = None

    def fit(self, X: np.ndarray, trained_through: date) -> None:
        from hmmlearn.hmm import GaussianHMM

        if X.ndim != 2 or X.shape[1] != 2:
            raise ValueError(
                f"Expected (N, 2) array, got shape {X.shape}"
            )
        if X.shape[0] < 100:
            raise ValueError(
                f"Need >=100 samples to fit, got {X.shape[0]}"
            )

        model = GaussianHMM(
            n_components=N_STATES,
            covariance_type=COVARIANCE_TYPE,
            n_iter=N_ITER_FIT,
            random_state=RANDOM_STATE,
        )

        # Warm-start from last persisted state if available.
        last = get_latest_hmm_state()
        if last is not None and last.trained_through < trained_through:
            try:
                model.startprob_ = np.array([0.5, 0.5])
                model.transmat_ = np.asarray(last.transmat)
                model.means_ = np.asarray(last.means)
                covars_arr = np.asarray(last.covars)
                if covars_arr.ndim == 3:
                    covars_arr = np.diagonal(
                        covars_arr, axis1=1, axis2=2,
                    )
                model.covars_ = covars_arr
                model.init_params = ""  # don't re-init
            except Exception as exc:
                _logger.warning(
                    "HMM warm-start failed, "
                    "falling back to cold init: %s",
                    exc,
                )

        model.fit(X)

        # Stable label ordering: state 0 = lower realized-vol mean.
        # X[:, 1] is the realized_vol_20d feature.
        means = model.means_
        if means[0, 1] > means[1, 1]:
            model.means_ = means[[1, 0]]
            model.transmat_ = (
                model.transmat_[[1, 0]][:, [1, 0]]
            )
            # ``covars_`` getter on a diag GaussianHMM returns a
            # 3D (n_states, n_dim, n_dim) array, but the setter
            # only accepts a 2D (n_states, n_dim) diag. Reduce
            # to 2D via diagonal extraction before swapping.
            cv = model.covars_
            if cv.ndim == 3:
                cv = np.diagonal(cv, axis1=1, axis2=2)
            model.covars_ = cv[[1, 0]]
            model.startprob_ = model.startprob_[[1, 0]]

        self._model = model
        self.means = model.means_.tolist()
        self.transmat = model.transmat_.tolist()
        # Always persist as full diag matrix for symmetry.
        cv = model.covars_
        if cv.ndim == 2:
            cv_full = np.zeros((N_STATES, 2, 2))
            for i in range(N_STATES):
                cv_full[i] = np.diag(cv[i])
            self.covars = cv_full.tolist()
        else:
            self.covars = cv.tolist()
        self.trained_through = trained_through

    def stress_prob(self, X_window: np.ndarray) -> float:
        """Return the posterior probability of being in the
        stressed state (state 1) on the LAST bar of the window.

        IMPORTANT: this uses ``predict_proba`` on the supplied
        window only — caller passes the trailing window
        ``X[:t+1]`` to enforce forward-only inference.
        """
        if self._model is None:
            raise RuntimeError("StressHMM not fitted")
        if X_window.ndim != 2 or X_window.shape[1] != 2:
            raise ValueError(
                f"Expected (N, 2) window, got {X_window.shape}"
            )
        proba = self._model.predict_proba(X_window)
        return float(proba[-1, 1])

    def save(self) -> None:
        if (
            self.transmat is None
            or self.means is None
            or self.covars is None
            or self.trained_through is None
        ):
            raise RuntimeError("Cannot save unfitted HMM")
        n_iter = int(getattr(
            self._model,
            "monitor_",
            type("M", (), {"iter": 0})(),
        ).iter)
        upsert_hmm_state(HmmStateRow(
            trained_through=self.trained_through,
            transmat=self.transmat,
            means=self.means,
            covars=self.covars,
            n_observations=n_iter,
        ))

    @classmethod
    def load(cls) -> "StressHMM | None":
        row = get_latest_hmm_state()
        if row is None:
            return None
        from hmmlearn.hmm import GaussianHMM

        inst = cls()
        model = GaussianHMM(
            n_components=N_STATES,
            covariance_type=COVARIANCE_TYPE,
        )
        model.startprob_ = np.array([0.5, 0.5])
        model.transmat_ = np.asarray(row.transmat)
        model.means_ = np.asarray(row.means)
        covars_arr = np.asarray(row.covars)
        if covars_arr.ndim == 3:
            covars_arr = np.diagonal(
                covars_arr, axis1=1, axis2=2,
            )
        model.covars_ = covars_arr
        inst._model = model
        inst.transmat = row.transmat
        inst.means = row.means
        inst.covars = row.covars
        inst.trained_through = row.trained_through
        return inst
