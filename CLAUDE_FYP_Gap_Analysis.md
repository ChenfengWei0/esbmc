# Gap Analysis: ESBMC Solidity Frontend vs. final-year-project-master

**Date:** 2026-04-03  
**Goal:** Verify all 5 source contracts in bounded and unbounded mode.  
**Scope:** Source contracts only (Foundry tests excluded — not a target).

---

## 1. Target Project Summary

The `final-year-project-master/` project is a Foundry-based property tokenization platform with 5 source contracts:

| Contract | LOC | Key Patterns |
|----------|-----|-------------|
| `PropertyToken.sol` | 186 | ERC20 inheritance, override, `_mint`, `_transfer`, `_approve`, dynamic array of owners |
| `PropertyTokenisation.sol` | 350 | Structs, enums, custom modifiers, mappings (string→struct), `abi.encodePacked`, `keccak256` |
| `PaymentEscrow.sol` | 430 | Structs, enums, `payable`, `address.transfer()`, while/for loops, ternary, `break`, storage refs |
| `AutomatedMarketMaker.sol` | 290 | Nested mappings, storage refs, Newton sqrt, `msg.value`, `payable(addr).transfer()` |
| `AutomatedMarketMakerSum.sol` | 260 | Similar to AMM, exchange rate math, `1e6`/`1e17` constants |

All contracts use: `pragma solidity ^0.8.19`, custom errors (`error Foo(...)`), `revert` with custom errors, events + `emit`, `msg.sender`, `block.timestamp`.

PropertyToken inherits OpenZeppelin v4 ERC20 (`contract PropertyToken is ERC20`).

---

## 2. Blocking Issues (Phase 0)

### 2.1 solc Import Resolution

**Problem:** ESBMC invokes solc as `solc --ast-compact-json <file>` without `--base-path`, so solc cannot resolve project-relative imports like `import {ERC20} from "lib/openzeppelin-contracts/..."`.

**Fix:** Detect the project root (by scanning for `foundry.toml`, `hardhat.config.*`, `remappings.txt`, or `package.json`) and pass `--base-path <root>` to solc.

**File:** `src/solidity-frontend/solidity_language.cpp:192`

### 2.2 OpenZeppelin Dependency Missing

**Problem:** `lib/openzeppelin-contracts/` is an empty git submodule directory.

**Fix:** Populate with OZ v4 ERC20 files (4 files, 517 LOC total). Only need:
- `contracts/token/ERC20/ERC20.sol`
- `contracts/token/ERC20/IERC20.sol`
- `contracts/token/ERC20/extensions/IERC20Metadata.sol`
- `contracts/utils/Context.sol`

---

## 3. OpenZeppelin Modeling Strategy

### Decision: Use Real OZ v4 (not abstractions)

**Considered alternatives:**
1. **Pure nondet** — `balanceOf()` returns nondet. **Rejected:** too imprecise, loses balance conservation semantics that all 5 contracts depend on.
2. **Function invariants** (`__ESBMC_assume` pre/post) — **Rejected:** fragile, hard to maintain, and the real ERC20 is simple enough to not need this.
3. **Concrete stub model** — simplified ERC20 with correct semantics. **Viable fallback** but adds a maintenance burden and risks divergence.
4. **Real OZ v4** — parse the actual 517 lines of OZ code. **Selected.**

**Rationale:**
- OZ v4 ERC20 is only 383 LOC with 3 tiny dependencies (IERC20, IERC20Metadata, Context)
- Uses no assembly, no try/catch, no complex patterns — just mappings, requires, unchecked blocks, events
- `_msgSender()` is literally `return msg.sender;`
- All functions use features our frontend already supports
- The only new frontend requirements are `interface` and `abstract contract` handling (minor — solc already flattens them into ContractDefinition nodes with `contractKind` field)
- Avoids model-vs-implementation divergence
- Generalizes to other OZ-using contracts in the future

**Fallback:** If interface/abstract contract support proves too complex for this iteration, create a flattened concrete model at the same path. The model would implement identical state and logic without the interface inheritance chain.

---

## 4. Feature Gap Matrix (Source Contracts)

### 4.1 Already Supported (no work needed)

| Feature | Used By |
|---------|---------|
| Binary/unary/ternary operators | All |
| if/else, for, while, do-while, break, continue | PaymentEscrow, AMMs |
| Mappings (incl. nested) | All except PropertyToken |
| Struct definitions and field access | PaymentEscrow, PropertyTokenisation, AMMs |
| Function visibility (public/external/internal/private) | All |
| view/pure modifiers | All |
| Literal units (1e6, 1e17) | AMMs |
| `msg.sender`, `msg.value`, `block.timestamp` | All |
| `address(this)` | PaymentEscrow, AMMs |
| `require()`, `assert()` | OZ ERC20 internally |
| keccak256, abi.encodePacked | PaymentEscrow, PropertyTokenisation |
| Custom modifiers with `_` placeholder | PropertyTokenisation |
| Event definitions and `emit` | All |
| Constructor with parameters | All |
| Inheritance (single) | PropertyToken |
| `unchecked {}` blocks | OZ ERC20 |
| `virtual`/`override` function dispatch | PropertyToken + OZ |
| Exponentiation `**` | AMMs |
| `type(uint256).max` | OZ ERC20 |
| Constant state variables | PropertyToken, PropertyTokenisation |

### 4.2 Gaps Requiring Implementation

| # | Feature | Gap Level | Used By | Notes |
|---|---------|-----------|---------|-------|
| G1 | **Custom error definitions** (`error Foo(...)`) | **Medium** | All 5 contracts | Grammar recognizes ErrorDef but decl handler is a no-op. Need: encode error type signature, support `revert Foo(args)` with error selector |
| G2 | **Enum definitions** | **Medium** | PaymentEscrow, PropertyTokenisation | Grammar recognizes EnumDef but decl handler is a no-op. Need: map enum values to uint8 constants, support enum comparison |
| G3 | **`interface` keyword** | **Medium** | OZ (IERC20, IERC20Metadata) | solc emits `contractKind: "interface"`. Frontend must handle: skip unimplemented function bodies, treat as virtual |
| G4 | **`abstract contract`** | **Medium** | OZ (Context is abstract in some versions) | Similar to interface — allow contracts with unimplemented virtual functions |
| G5 | **Multiple inheritance with interfaces** | **Medium** | OZ (`ERC20 is Context, IERC20, IERC20Metadata`) | Inheritance flattening exists but needs testing with interface/abstract mix |
| G6 | **Storage reference locals** | **Medium** | PaymentEscrow, AMMs | `Order storage o = orders[i]` must alias, not copy. Infrastructure exists but unverified |
| G7 | **`new Contract()` expression** | **High** | PropertyTokenisation deploys PropertyToken | Needs: allocate fresh address, run constructor, return contract instance. Critical for inter-contract testing |
| G8 | **`address.transfer()` / `payable()` cast** | **Low-Med** | PaymentEscrow, AMMs | `payable(addr).transfer(amt)` — transfer is modeled, payable cast needs verification |
| G9 | **Array `.push()` / `.pop()`** | **Low-Med** | PaymentEscrow, PropertyToken | Infrastructure exists, edge cases with struct arrays need testing |
| G10 | **`bytes()` type conversion** | **Low** | PropertyTokenisation | `bytes(str).length == 0` pattern for empty string check |

### 4.3 Not Needed (Foundry-only features, excluded from scope)

- `vm.prank()`, `vm.deal()`, `vm.expectRevert()` (Foundry cheatcodes)
- `Test`/`StdCheats` inheritance (forge-std framework)
- `makeAddr()`, `vm.addr()` (test helpers)
- `{value: amount}` call syntax (only in tests)
- Multiple contract deployment in test harnesses

---

## 5. Recommended Implementation Order

### Phase 0 — Unblock Parsing (immediate)
1. Fix `invoke_solc()` to pass `--base-path` for project-relative imports
2. Populate OZ v4 files in `final-year-project-master/lib/`

### Phase 1 — Core Language Gaps
3. **G2: Enum support** — map to uint8 constants, support comparison
4. **G1: Custom error support** — encode error definitions, wire `revert Foo(args)`
5. **G3+G4: Interface/abstract handling** — treat as contract, skip empty bodies
6. **G5: Test multi-interface inheritance** with real OZ chain

### Phase 2 — Runtime Semantics
7. **G6: Storage reference locals** — verify alias semantics
8. **G8: payable cast + transfer** — verify end-to-end
9. **G9: Array push/pop with structs** — test edge cases
10. **G10: bytes() conversion** — support string-to-bytes cast

### Phase 3 — Inter-contract
11. **G7: `new Contract()` expression** — needed only for PropertyTokenisation deploying PropertyToken. Can defer by using a verification harness that pre-deploys contracts.

---

## 6. Estimated Effort

| Phase | Scope | Effort |
|-------|-------|--------|
| Phase 0 | solc import fix + OZ files | Small (1 session) |
| Phase 1 | Enums, errors, interfaces | Medium (2-3 sessions) |
| Phase 2 | Storage refs, payable, arrays | Medium (2-3 sessions) |
| Phase 3 | new Contract() | Large (defer if possible) |

**Overall:** With Phase 0-2 complete, we can verify PropertyToken, PropertyTokenisation (with manual harness), and the AMM contracts. Phase 3 is only needed for full PropertyTokenisation self-deploy verification.
