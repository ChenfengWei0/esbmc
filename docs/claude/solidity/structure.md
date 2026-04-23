# Solidity Frontend — File Structure

The core converter is a single class `solidity_convertert` (declared in `solidity_convert.h`), with implementations split by concern across multiple `.cpp` files:

| File | Purpose |
|------|---------|
| `solidity_convert.cpp` | Entry point, initialization, AST preprocessing |
| `solidity_convert_expr.cpp` | Expression conversion (`get_expr` and operator helpers) |
| `solidity_convert_call.cpp` | Function calls, transfers, high/low-level calls |
| `solidity_convert_type.cpp` | Type descriptions, elementary type mapping, parameter lists |
| `solidity_convert_decl.cpp` | Declarations: variables, functions, structs, contracts, enums |
| `solidity_convert_util.cpp` | Utilities: locations, symbols, JSON helpers, array helpers |
| `solidity_convert_constructor.cpp` | Constructor creation, initializer migration, contract instantiation, `_ESBMC_clone_<C>` / `_ESBMC_state_forward_<C>` helpers |
| `solidity_convert_contract.cpp` | Contract instances, multi-transaction/multi-contract verification |
| `solidity_convert_ref.cpp` | Symbol resolution: variable/function/enum/builtin references |
| `solidity_convert_mapping.cpp` | Solidity `mapping` to infinite array conversion |
| `solidity_convert_stmt.cpp` | Statement and block conversion |
| `solidity_convert_modifier.cpp` | Function modifiers, reentrancy checks |
| `solidity_convert_builtin.cpp` | Built-in symbols/properties (msg, tx, block, address) |
| `solidity_convert_tuple.cpp` | Tuple definition, instantiation, assignment unpacking |
| `solidity_convert_inheritance.cpp` | Contract inheritance handling |
| `solidity_convert_literals.cpp` | Literal conversion (integer, bool, string, hex) |
| `solidity_grammar.cpp/h` | Grammar enums, `SolType` enum, type mapping and classification functions |
| `solidity_language.cpp/h` | Language plugin interface |
| `solidity_tod_analysis.{cpp,h}` | R/W footprint analysis + call-graph closure for TOD detection |
| `solidity_tod_harness.{cpp,h}` | TOD harness generator (two-copy contract rename + assertion emission) |
| `solidity_monomorphize.cpp` | Template-style monomorphisation helpers |
| `pattern_check.cpp/h` | Vulnerability pattern detection (e.g. SWC-115 tx.origin) |
| `typecast.{cpp,h}` | Typecast helpers |
