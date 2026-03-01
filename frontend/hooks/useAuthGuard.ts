"use client";
/**
 * Hook that redirects to /login when the JWT access token is missing or
 * expired.  Must be called at the top level of a page component.
 */

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { getAccessToken, isTokenExpired } from "@/lib/auth";

export function useAuthGuard() {
  const router = useRouter();

  useEffect(() => {
    const token = getAccessToken();
    if (!token || isTokenExpired(token)) {
      router.replace("/login");
    }
  }, [router]);
}
