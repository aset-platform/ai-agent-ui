# Serena Tool Usage Reference

## Key Parameter Names (easy to get wrong)
- `find_symbol` uses **`name_path_pattern`** (NOT `name_path`)
- `get_symbols_overview` uses `relative_path` + `depth`
- `find_referencing_symbols` uses `name` + `relative_path`

## Common Workflows

### Read a file's structure
```
get_symbols_overview(relative_path="path/to/file.py", depth=1)
```

### Read a specific method body
```
find_symbol(name_path_pattern="ClassName/method_name", relative_path="path/to/file.py", include_body=True)
```

### Find all references to a symbol
```
find_referencing_symbols(name="SymbolName", relative_path="path/to/file.py")
```

### Edit a symbol
```
replace_symbol_body(...)       # replace entire symbol
insert_before_symbol(...)      # add code before
insert_after_symbol(...)       # add code after
rename_symbol(...)             # rename + update references
```

## Tips
- Start with `depth=0` (top-level), then `depth=1` (class methods)
- Use `include_body=False` first to see what exists, then `True` for specific methods
- `name_path_pattern` supports hierarchy: `ClassName/method` or just `function_name`
- Use `search_for_pattern` when you don't know the symbol name
