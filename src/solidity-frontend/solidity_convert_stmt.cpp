/// \file solidity_convert_stmt.cpp
/// \brief Statement conversion for the Solidity frontend.
///
/// Converts Solidity statements (blocks, if/else, for, while, do-while,
/// return, break, continue, emit, revert, require/assert, variable
/// declaration statements, expression statements, and try-catch) from
/// the solc JSON AST into ESBMC's GOTO-level code representation.

#include <solidity-frontend/solidity_convert.h>
#include <solidity-frontend/typecast.h>
#include <util/arith_tools.h>
#include <util/bitvector.h>
#include <util/c_types.h>
#include <util/expr_util.h>
#include <util/i2string.h>
#include <util/mp_arith.h>
#include <util/std_expr.h>
#include <util/message.h>
#include <fstream>
#include <set>

void solidity_convertert::reset_auxiliary_vars()
{
  current_baseContractName = "";
  current_functionName = "";
  current_functionDecl = nullptr;
  current_forStmt = nullptr;
  initializers.clear();
}

bool solidity_convertert::get_function_params(
  const nlohmann::json &pd,
  const std::string &cname,
  exprt &param)
{
  // 1. get parameter type
  // Route through the decl-aware overload when the parameter AST carries a
  // typeName node. Mirrors get_parameter_list (used for return types) and
  // get_var_decl (used for locals); both reach get_array_pointer_type for
  // array shapes, which lowers fixed and dynamic arrays uniformly to
  // pointers. The plain `pd["typeDescriptions"]` form falls into the
  // typeIdentifier-string parser, which builds a real array_typet for
  // fixed-outer NestedArrayTypeName and disagrees with the local-var /
  // call-site representation — see `f(uint[3][2] calldata s)`, where the
  // caller passes `uint**` but the parameter symbol used to be `uint[3][2]`.
  typet param_type;
  if (pd.contains("typeName"))
  {
    if (get_type_description(
          pd, pd["typeName"]["typeDescriptions"], param_type))
      return true;
  }
  else
  {
    if (get_type_description(pd["typeDescriptions"], param_type))
      return true;
  }

  // 2a. get id and name
  std::string id, name;
  assert(current_functionName != ""); // we are converting a function param now
  assert(current_functionDecl);
  get_local_var_decl_name(pd, cname, name, id);
  // 2b. handle Omitted Names in Function Definitions
  if (name == "")
  {
    // Items with omitted names will still be present on the stack, but they are inaccessible by name.
    // e.g. ~omitted1, ~omitted2. which is a invalid name for solidity.
    // Therefore it won't conflict with other arg names.
    //log_error("Omitted params are not supported");
    // return true;
    ;
  }

  param = code_typet::argumentt();
  param.type() = param_type;
  param.cmt_base_name(name);

  // 3. get location
  locationt location_begin;
  get_location_from_node(pd, location_begin);

  param.cmt_identifier(id);
  param.location() = location_begin;

  // 4. get symbol
  std::string debug_modulename =
    get_modulename_from_path(location_begin.file().as_string());
  symbolt param_symbol;
  get_default_symbol(
    param_symbol, debug_modulename, param_type, name, id, location_begin);

  // 5. set symbol's lvalue, is_parameter and file local
  param_symbol.lvalue = true;
  param_symbol.is_parameter = true;
  param_symbol.file_local = true;

  // 6. add symbol to the context
  move_symbol_to_context(param_symbol);

  return false;
}

bool solidity_convertert::get_block(
  const nlohmann::json &block,
  exprt &new_expr)
{
  // For rule block
  locationt location;
  get_start_location_from_stmt(block, location);

  SolidityGrammar::BlockT type = SolidityGrammar::get_block_t(block);
  log_debug(
    "solidity",
    "	@@@ got Block: SolidityGrammar::BlockT::{}",
    SolidityGrammar::block_to_str(type));

  switch (type)
  {
  // equivalent to clang::Stmt::CompoundStmtClass
  // deal with a block of statements
  case SolidityGrammar::BlockT::Statement:
  {
    const nlohmann::json &stmts = block["statements"];

    // Track unchecked blocks: save/restore flag using RAII pattern
    const bool is_unchecked = (block["nodeType"] == "UncheckedBlock");
    const bool prev_unchecked = in_unchecked_block;
    if (is_unchecked)
      in_unchecked_block = true;

    code_blockt _block;
    unsigned ctr = 0;
    // items() returns a key-value pair with key being the index
    for (auto const &stmt_kv : stmts.items())
    {
      locationt cl;
      get_location_from_node(stmt_kv.value(), cl);
      if (in_unchecked_block)
        cl.set("#sol_unchecked", "1");

      exprt statement;
      if (get_statement(stmt_kv.value(), statement))
        return true;

      if (!expr_frontBlockDecl.operands().empty())
      {
        for (auto op : expr_frontBlockDecl.operands())
        {
          convert_expression_to_code(op);
          _block.operands().push_back(op);
        }
        expr_frontBlockDecl.clear();
      }
      statement.location() = cl;
      convert_expression_to_code(statement);
      _block.operands().push_back(statement);

      if (!expr_backBlockDecl.operands().empty())
      {
        for (auto op : expr_backBlockDecl.operands())
        {
          convert_expression_to_code(op);
          _block.operands().push_back(op);
        }
        expr_backBlockDecl.clear();
      }

      // Per-rollback granularity for B1's revert state-rollback:
      // OR `statement_is_mutation_top_level` of the AST statement we
      // just lowered into `current_function_seen_mutation` so the next
      // `build_revert_rollback_block` invocation knows whether to
      // emit the full `*this = save; return [nondet]` form (mutation
      // observed earlier) or the cheap `return [nondet]` early-return
      // form.  Done after the lowering so any require/revert *inside*
      // this statement reads the prior value of the flag (the
      // mutation in this statement only counts toward subsequent
      // statements).  The flag is itself reset on entry to each
      // function in get_function_definition.
      if (statement_is_mutation_top_level(stmt_kv.value()))
        current_function_seen_mutation = true;

      ++ctr;
    }
    log_debug("solidity", " \t@@@ CompoundStmt has {} statements", ctr);

    locationt location_end;
    get_final_location_from_stmt(block, location_end);

    _block.end_location(location_end);
    new_expr = _block;

    // Restore unchecked flag
    in_unchecked_block = prev_unchecked;
    break;
  }
  case SolidityGrammar::BlockT::BlockForStatement:
  case SolidityGrammar::BlockT::BlockIfStatement:
  case SolidityGrammar::BlockT::BlockWhileStatement:
  case SolidityGrammar::BlockT::BlockDoWhileStatement:
  {
    // this means only one statement in the block
    exprt statement;

    // pass directly to get_statement()
    if (get_statement(block, statement))
      return true;
    convert_expression_to_code(statement);
    new_expr = statement;
    break;
  }
  case SolidityGrammar::BlockT::BlockExpressionStatement:
  {
    get_expr(block["expression"], new_expr);
    break;
  }
  case SolidityGrammar::BlockT::BlockTError:
  default:
  {
    log_error("Unimplemented type in rule block");
    return true;
  }
  }

  new_expr.location() = location;
  return false;
}

bool solidity_convertert::get_statement(
  const nlohmann::json &stmt,
  exprt &new_expr)
{
  // For rule statement
  // Since this is an additional layer of grammar rules compared to clang C, we do NOT set location here.
  // Just pass the new_expr reference to the next layer.

  locationt loc;
  get_location_from_node(stmt, loc);

  SolidityGrammar::StatementT type = SolidityGrammar::get_statement_t(stmt);
  log_debug(
    "solidity",
    "	@@@ got Stmt: SolidityGrammar::StatementT::{}",
    SolidityGrammar::statement_to_str(type));

  switch (type)
  {
  case SolidityGrammar::StatementT::Block:
  {
    if (get_block(stmt, new_expr))
      return true;
    break;
  }
  case SolidityGrammar::StatementT::ExpressionStatement:
  {
    // Bare type-valued expressions such as `Arst.Foo;` or `C;` have no
    // runtime effect — their typeString is `type(...)`. Trying to lower
    // them through the normal ContractMemberCall / Identifier paths
    // trips on JSON fields that only exist for value-producing member
    // accesses (e.g. a struct-definition member looked up via
    // `contract_var.structName`). Skip them cleanly here.
    {
      const nlohmann::json &inner = stmt["expression"];
      if (
        inner.contains("typeDescriptions") &&
        inner["typeDescriptions"].contains("typeString") &&
        inner["typeDescriptions"]["typeString"].is_string())
      {
        const std::string ts =
          inner["typeDescriptions"]["typeString"].get<std::string>();
        if (ts.compare(0, 5, "type(") == 0)
        {
          new_expr = code_skipt();
          break;
        }
      }
    }
    if (get_expr(
          stmt["expression"], stmt["expression"]["typeDescriptions"], new_expr))
      return true;
    // Bare expression-statement forms that reduce to no-op exprt shapes
    // (e.g. `MyAddress.wrap;` — a MemberAccess naming a UDVT wrap/unwrap
    // function reference without invoking it) produce a typecast_exprt
    // whose op0 is never populated by the wrap/unwrap handler in
    // solidity_convert_ref.cpp (the comment there says "we will set the
    // op0 later"; "later" only happens inside the FunctionCall path).
    // typecast_exprt's constructor leaves op0 as a default-constructed
    // empty exprt, which has no meaningful side effect and trips
    // migrate_expr during GOTO generation. Replace with a skip.
    if (new_expr.id() == "typecast" &&
        (new_expr.operands().empty() ||
         new_expr.op0().id().as_string().empty()))
      new_expr = code_skipt();
    // Bare value-expression statements (`this;`, `hax;`, `tuple_instance$0;`)
    // are used in Solidity to suppress unused-variable / state-mutability
    // warnings; they have no side effects. Without this, they reach symex
    // as raw symbol exprs in OTHER instructions and trip
    // `goto_symext: unexpected statement: symbol`.
    if (new_expr.id() == "symbol")
      new_expr = code_skipt();
    break;
  }
  case SolidityGrammar::StatementT::VariableDeclStatement:
  {
    const nlohmann::json &declgroup = stmt["declarations"];

    codet decls("decl-block");
    unsigned ctr = 0;
    // N.B. Although Solidity AST JSON uses "declarations": [],
    // the size of this array is alway 1!
    // A second declaration will become another stmt in "statements" array
    // e.g. "statements" : [
    //  {"declarations": [], "id": 1}
    //  {"declarations": [], "id": 2}
    //  {"declarations": [], "id": 3}
    // ]
    if (declgroup.size() == 1)
    {
      // deal with local var decl with init value
      const nlohmann::json &decl = declgroup[0];
      nlohmann::json initialValue = nlohmann::json::object();
      if (stmt.contains("initialValue"))
        initialValue = stmt["initialValue"];

      exprt single_decl;
      if (get_var_decl(decl, initialValue, single_decl))
        return true;

      decls.operands().push_back(single_decl);
      ++ctr;
    }
    else
    {
      // separate the decl and assignment
      for (const auto &it : declgroup.items())
      {
        if (it.value().is_null() || it.value().empty())
          continue;
        const nlohmann::json &decl = it.value();
        exprt single_decl;
        if (get_var_decl(decl, single_decl))
          return true;
        decls.operands().push_back(single_decl);
        ++ctr;
      }

      if (stmt.contains("initialValue"))
      {
        // this is a tuple expression
        const nlohmann::json &initialValue = stmt["initialValue"];
        exprt tuple_expr;
        if (get_expr(initialValue, tuple_expr))
          return true;

        // Build LHS block preserving original positions so that omitted
        // elements (null in declarations) map to nil_exprt at the correct
        // index.  construct_tuple_assigments uses positional "mem{i}" keys,
        // so the indices must match the RHS tuple struct layout.
        code_blockt lhs_block;
        unsigned decl_idx = 0;
        for (const auto &it : declgroup.items())
        {
          if (it.value().is_null() || it.value().empty())
          {
            lhs_block.copy_to_operands(nil_exprt());
          }
          else
          {
            assert(decl_idx < decls.operands().size());
            lhs_block.copy_to_operands(
              decls.operands()[decl_idx].op0());
            ++decl_idx;
          }
        }

        construct_tuple_assigments(stmt, lhs_block, tuple_expr);
      }
    }
    log_debug("solidity", " \t@@@ DeclStmt group has {} decls", ctr);

    new_expr = decls;
    break;
  }
  case SolidityGrammar::StatementT::ReturnStatement:
  {
    if (!current_functionDecl)
    {
      log_error(
        "Error: ESBMC could not find the parent scope for this "
        "ReturnStatement");
      return true;
    }

    // 1. get return type
    // TODO: Fix me! Assumptions:
    //  a). It's "return <expr>;" not "return;"
    //  b). <expr> is pointing to a DeclRefExpr, we need to wrap it in an ImplicitCastExpr as a subexpr
    //  c). For multiple return type, the return statement represented as a tuple expression using a components field.
    //      Besides, tuple can only be declared literally. https://docs.soliditylang.org/en/latest/control-structures.html#assignment
    //      e.g. return (false, 123)
    if (!stmt.contains("expression"))
    {
      // "return;"
      code_returnt ret_expr;
      new_expr = ret_expr;
      return false;
    }
    assert(stmt["expression"].contains("nodeType"));

    // get_type_description
    typet return_exrp_type;
    if (get_type_description(
          stmt["expression"]["typeDescriptions"], return_exrp_type))
      return true;

    // `return f();` where f is itself void: solc gives the call expression
    // typeString "tuple()" (an empty tuple). The function being compiled
    // still has zero return parameters, so there is no tuple_instance to
    // assign into — emit the inner call as a statement and a bare `return;`.
    if (
      stmt["expression"].contains("typeDescriptions") &&
      stmt["expression"]["typeDescriptions"].value("typeString", "") ==
        "tuple()")
    {
      exprt inner;
      if (get_expr(stmt["expression"], inner))
        return true;
      code_blockt block;
      convert_expression_to_code(inner);
      block.copy_to_operands(inner);
      block.copy_to_operands(code_returnt());
      new_expr = block;
      return false;
    }

    if (get_sol_type(return_exrp_type) == SolidityGrammar::SolType::TUPLE_RETURNS)
    {
      if (
        stmt["expression"]["nodeType"].get<std::string>() !=
          "TupleExpression" &&
        stmt["expression"]["nodeType"].get<std::string>() != "FunctionCall")
      {
        log_error("Unexpected tuple");
        return true;
      }

      // get tuple instance
      std::string tname, tid;
      if (get_tuple_instance_name(*current_functionDecl, tname, tid))
        return true;
      if (context.find_symbol(tid) == nullptr)
      {
        log_error("cannot find tuple instance symbol: {}", tid);
        return true;
      }

      // get lhs
      exprt lhs = symbol_expr(*context.find_symbol(tid));

      if (
        stmt["expression"]["nodeType"].get<std::string>() == "TupleExpression")
      {
        // return (x,y) ==>
        // tuple.mem0 = x; tuple.mem1 = y; return ;

        // get rhs
        // hack: we need the expression block, not tuple instance
        current_lhsDecl = true;
        exprt rhs;
        if (get_expr(stmt["expression"], rhs))
          return true;
        current_lhsDecl = false;

        size_t ls = to_struct_type(lhs.type()).components().size();
        size_t rs = rhs.operands().size();
        if (ls != rs)
        {
          log_debug(
            "soldiity",
            "Handling return tuple.\nlhs = {}\nrhs = {}",
            lhs.to_string(),
            rhs.to_string());
          log_error("Internal tuple error.");
        }

        for (size_t i = 0; i < ls; i++)
        {
          // lop: struct member call (e.g. tuple.men0)
          exprt lop;
          if (get_tuple_member_call(
                lhs.identifier(),
                to_struct_type(lhs.type()).components().at(i),
                lop))
            return true;

          // rop: constant/symbol
          exprt rop = rhs.operands().at(i);

          // do assignment
          get_tuple_assignment(stmt, lop, rop);
        }
      }
      else
      {
        // return func(); ==>
        // tuple1.mem0 = tuple0.mem0; return;

        // get rhs
        exprt rhs;
        bool rhs_is_nondet = false;
        if (get_tuple_function_ref(stmt["expression"]["expression"], rhs))
        {
          // Builtin tuple-producing callee (e.g. `addr.call(...)` returning
          // `(bool, bytes memory)`) has no referencedDeclaration and no
          // tuple_instance symbol. Fall back to nondet-per-member.
          rhs_is_nondet = true;
        }

        // add function call
        //
        // Only when we have a real tuple instance for the callee (i.e.
        // !rhs_is_nondet) do we need to materialise the function call
        // and emit it via get_tuple_function_call. For builtin tuple-
        // producing callees (`addr.call(...)`, `abi.decode(data, (T,U))`
        // etc.) the result is over-approximated by independent nondet
        // per member, so the call exprt itself is never used downstream.
        // Skipping get_expr in that case also avoids migrate failures
        // when the builtin's lowered return type (e.g. abi_decode's
        // single-uint256 identity) does not match the Solidity tuple
        // shape expected by the rest of the return path.
        exprt func_call;
        if (!rhs_is_nondet)
        {
          if (get_expr(
                stmt["expression"],
                stmt["expression"]["typeDescriptions"],
                func_call))
            return true;
          get_tuple_function_call(func_call);
        }

        size_t ls = to_struct_type(lhs.type()).components().size();
        size_t rs =
          rhs_is_nondet ? ls : to_struct_type(rhs.type()).components().size();
        if (ls != rs)
        {
          log_error("Unexpected tuple structure");
          abort();
        }

        for (size_t i = 0; i < ls; i++)
        {
          // lop: struct member call (e.g. tupleA.men0)
          exprt lop;
          if (get_tuple_member_call(
                lhs.identifier(),
                to_struct_type(lhs.type()).components().at(i),
                lop))
            return true;

          // rop: struct member call (e.g. tupleB.men0), or nondet fallback
          exprt rop;
          if (rhs_is_nondet)
          {
            get_nondet_expr(lop.type(), rop);
          }
          else if (get_tuple_member_call(
                rhs.identifier(),
                to_struct_type(rhs.type()).components().at(i),
                rop))
            return true;

          // do assignment
          get_tuple_assignment(stmt, lop, rop);
        }
      }
      // do return in the end
      exprt return_expr = code_returnt();
      move_to_back_block(return_expr);

      new_expr = code_skipt();
      break;
    }

    typet return_type;
    // When inlining a delegate-shadow body, the return statement belongs to
    // the target function, not the caller. Use the override set by
    // try_get_delegate_shadow_call so we pick up the target's return type.
    const nlohmann::json *ret_params_src =
      delegate_shadow_target_return_params != nullptr
        ? delegate_shadow_target_return_params
        : ((*current_functionDecl).contains("returnParameters")
             ? &(*current_functionDecl)["returnParameters"]
             : nullptr);
    if (ret_params_src != nullptr)
    {
      // Previously this block asserted that
      // current_functionDecl.returnParameters.id ==
      // stmt.functionReturnParameters, to catch cases where a Return node
      // refers to a different ParameterList than its enclosing function.
      // That invariant does not hold on real-world solc 0.6.x output for
      // contracts that inline base-contract function bodies via modifiers
      // or internal trampolines (observed on 1inch Mooniswap). Type
      // extraction below only uses ret_params_src, so the id mismatch is
      // harmless — rely on get_type_description and drop the assert.
      if (get_type_description(*ret_params_src, return_type))
        return true;
    }
    else
      return true;

    nlohmann::json literal_type = nullptr;

    auto expr_type = SolidityGrammar::get_expression_t(stmt["expression"]);
    bool expr_is_literal = expr_type == SolidityGrammar::Literal;
    if (expr_is_literal)
      literal_type = make_return_type_from_typet(return_type);

    // 2. get return value
    code_returnt ret_expr;
    const nlohmann::json &rtn_expr = stmt["expression"];
    // wrap it in an ImplicitCastExpr to convert LValue to RValue
    nlohmann::json implicit_cast_expr =
      make_implicit_cast_expr(rtn_expr, "LValueToRValue");

    /* There could be case like
      {
      "expression": {
          "kind": "number",
          "nodeType": "Literal",
          "typeDescriptions": {
              "typeIdentifier": "t_rational_11_by_1",
              "typeString": "int_const 11"
          },
          "value": "12345"
      },
      "nodeType": "Return",
      }
      Therefore, we need to pass the literal_type value.
      */

    exprt rhs;
    if (get_expr(implicit_cast_expr, literal_type, rhs))
      return true;

    // If rhs is an inline array literal (id("array")) being returned
    // from a fixed-size Solidity array return type, the frontend models
    // the return type as a pointer and c_typecast's array→pointer decay
    // produces `&{...}[0]` — an address-of indexing into an inline
    // initializer that the dereference pass cannot simplify, recursing
    // unboundedly on member/index chains nested under the literal.
    //
    // Materialize the literal into a static auxiliary array first, then
    // let the regular implicit cast turn that aux symbol into a normal
    // pointer.
    if (
      rhs.id() == "array" && return_type.id() == typet::id_pointer &&
      rhs.type().is_array())
    {
      const typet &elem_type = return_type.subtype();
      for (auto &op : rhs.operands())
        solidity_gen_typecast(ns, op, elem_type);
      // Make sure the array's element type matches the destination
      // before stamping it into a static aux symbol.
      to_array_type(rhs.type()).subtype() = elem_type;
      exprt aux;
      get_aux_array(rhs, elem_type, aux);
      rhs = aux;
    }
    // `return "literal";` from a function returning `bytes memory` produces
    // a plain string_constantt (array of signed char). c_typecast cannot
    // convert that to a BytesDynamic struct, so the raw byte array would
    // leak into the GOTO `RETURN` and crash symex on the type mismatch.
    // Narrow the repair to cases where rhs is actually a string constant
    // (array of signed char) so that builtins already returning a pointer
    // or BytesDynamic keep their existing path.
    bool rhs_is_string_constant =
      rhs.id() == "string-constant" ||
      (rhs.type().is_array() && rhs.type().subtype().is_signedbv() &&
       to_signedbv_type(rhs.type().subtype()).get_width() == 8);
    // Same OVER-approximation for "scalar uint256 returned where the
    // function declares `bytes memory`": canonical case is
    //   function f() ... returns (bytes memory) {
    //     return abi.encodeCall(this.g, (...));
    //   }
    // where abi.encodeCall is the uint256 identity in solidity_abi.c.
    // c_typecast cannot promote a 256-bit scalar into a BytesDynamic
    // struct, and any naive cast leaks the raw int through `RETURN`
    // — symex then segfaults on the struct-vs-scalar shape mismatch
    // when the caller assigns it into a BytesDynamic temporary.
    bool rhs_is_scalar_to_bytes =
      get_sol_type(return_type) == SolidityGrammar::SolType::BYTES_DYN &&
      !rhs.type().is_pointer() && !rhs.type().is_array() &&
      !rhs.type().is_struct() && !rhs_is_string_constant;
    if (
      get_sol_type(return_type) == SolidityGrammar::SolType::BYTES_DYN &&
      (rhs_is_string_constant || rhs_is_scalar_to_bytes))
    {
      // convert_type_expr aborts when no containing contract supplies a
      // dynamic pool (e.g. free functions declared outside any contract
      // via `using { f } for T;`). In that case the precise string→bytes
      // conversion is not representable, so we over-approximate the
      // return value with llc_nondet_bytes() — a BytesDynamic with
      // fully-nondet length (post-T1.2) and initialized == 1.
      // [APPROX: OVER] Recorded in approximation ledger #21.
      exprt dummy_pool;
      if (get_dynamic_pool(stmt["expression"], dummy_pool))
      {
        locationt loc;
        get_start_location_from_stmt(stmt, loc);
        side_effect_expr_function_callt nondet_b;
        get_library_function_call_no_args(
          "llc_nondet_bytes",
          "c:@F@llc_nondet_bytes",
          return_type,
          loc,
          nondet_b);
        rhs = nondet_b;
      }
      else
        convert_type_expr(ns, rhs, return_type, stmt["expression"]);
    }
    else if (
      get_sol_type(return_type) == SolidityGrammar::SolType::BYTES_STATIC &&
      !rhs.type().is_struct())
    {
      // `return 0;` (or any scalar) from a function declared to return
      // bytesN. The bytesN return type is modeled as a BytesStatic struct,
      // but the literal/scalar rhs is an integer. solidity_gen_typecast
      // would emit a raw typecast(int -> struct); the value-set analysis
      // then walks make_member through that typecast onto a non-struct
      // operand and aborts (Release: SIGSEGV) in value_sett::make_member.
      // Route through convert_type_expr, which lowers int -> BytesStatic
      // via bytes_static_from_uint, producing a real struct value. Mirrors
      // the BYTES_DYN handling above.
      convert_type_expr(ns, rhs, return_type, stmt["expression"]);
    }
    else
      solidity_gen_typecast(ns, rhs, return_type);
    ret_expr.return_value() = rhs;

    new_expr = ret_expr;

    break;
  }
  case SolidityGrammar::StatementT::ForStatement:
  {
    // Based on rule for-statement

    // For nested loop
    const nlohmann::json *old_forStmt = current_forStmt;
    current_forStmt = &stmt;

    // 1. annotate init
    codet init =
      code_skipt(); // code_skipt() means no init in for-stmt, e.g. for (; i< 10; ++i)
    if (stmt.contains("initializationExpression"))
      if (get_statement(stmt["initializationExpression"], init))
        return true;

    convert_expression_to_code(init);

    // 2. annotate condition
    exprt cond = true_exprt();
    if (stmt.contains("condition"))
      if (get_expr(stmt["condition"], cond))
        return true;

    // 3. annotate increment
    codet inc = code_skipt();
    if (stmt.contains("loopExpression"))
      if (get_statement(stmt["loopExpression"], inc))
        return true;

    convert_expression_to_code(inc);

    // 4. annotate body
    codet body = code_skipt();
    if (stmt.contains("body"))
      if (get_statement(stmt["body"], body))
        return true;

    convert_expression_to_code(body);

    code_fort code_for;
    code_for.init() = init;
    code_for.cond() = cond;
    code_for.iter() = inc;
    code_for.body() = body;

    new_expr = code_for;
    current_forStmt = old_forStmt;
    break;
  }
  case SolidityGrammar::StatementT::IfStatement:
  {
    // Based on rule if-statement
    // 1. Condition: make a exprt for condition
    exprt cond;
    if (get_expr(stmt["condition"], cond))
      return true;

    // 2. Then: make a exprt for trueBody
    exprt then;
    if (get_statement(stmt["trueBody"], then))
      return true;

    convert_expression_to_code(then);

    codet if_expr("ifthenelse");
    if_expr.copy_to_operands(cond, then);

    // 3. Else: make a exprt for "falseBody" if the if-statement node contains an "else" block.
    // solc 0.6.x always emits the field, with `null` when there is no else;
    // 0.8.x omits it. Treat both as "no else".
    if (stmt.contains("falseBody") && !stmt["falseBody"].is_null())
    {
      exprt else_expr;
      if (get_statement(stmt["falseBody"], else_expr))
        return true;

      convert_expression_to_code(else_expr);
      if_expr.copy_to_operands(else_expr);
    }

    new_expr = if_expr;
    break;
  }
  case SolidityGrammar::StatementT::WhileStatement:
  {
    exprt cond = true_exprt();
    if (get_expr(stmt["condition"], cond))
      return true;

    codet body = codet();
    if (get_block(stmt["body"], body))
      return true;

    convert_expression_to_code(body);

    code_whilet code_while;
    code_while.cond() = cond;
    code_while.body() = body;

    new_expr = code_while;
    break;
  }
  case SolidityGrammar::StatementT::DoWhileStatement:
  {
    exprt cond = true_exprt();
    if (get_expr(stmt["condition"], cond))
      return true;

    codet body = codet();
    if (get_block(stmt["body"], body))
      return true;

    convert_expression_to_code(body);

    code_dowhilet code_dowhile;
    code_dowhile.cond() = cond;
    code_dowhile.body() = body;

    new_expr = code_dowhile;
    break;
  }
  case SolidityGrammar::StatementT::ContinueStatement:
  {
    new_expr = code_continuet();
    break;
  }
  case SolidityGrammar::StatementT::BreakStatement:
  {
    new_expr = code_breakt();
    break;
  }
  case SolidityGrammar::StatementT::RevertStatement:
  {
    // e.g.
    // {
    //   "errorCall": {
    //     "nodeType": "FunctionCall",
    //   }
    //   "nodeType": "RevertStatement",
    // }
    if (!stmt.contains("errorCall") || get_expr(stmt["errorCall"], new_expr))
      return true;

    break;
  }
  case SolidityGrammar::StatementT::EmitStatement:
  {
    // treat emit as function call
    if (!stmt.contains("eventCall"))
    {
      log_error("Unexpected emit statement.");
      return true;
    }
    if (get_expr(stmt["eventCall"], new_expr))
      return true;

    break;
  }
  case SolidityGrammar::StatementT::PlaceholderStatement:
  {
    code_skipt placeholder;
    placeholder.set("#is_modifier_placeholder", true);
    new_expr = placeholder;
    break;
  }
  case SolidityGrammar::StatementT::TryStatement:
  {
    // Model try/catch as:
    //   if (nondet_bool()) { <externalCall>; <success_body> }
    //   else               { <catch_block(s)> }
    //
    // The success branch actually executes the external call so its
    // side effects are visible; if the call internally reverts, the
    // usual `__ESBMC_assume(false)` propagation prunes the success path
    // and only the catch branch remains feasible.  Return values of the
    // call are still modelled as nondet in the success parameter bindings
    // (cross-contract resolution is out of scope for the AST-level frontend).

    if (!stmt.contains("clauses") || !stmt["clauses"].is_array() ||
        stmt["clauses"].size() < 2)
    {
      log_error("TryStatement must have at least 2 clauses "
                "(success + catch)");
      return true;
    }

    const auto &clauses = stmt["clauses"];

    // --- success branch (first clause) ---
    const auto &success_clause = clauses[0];
    code_blockt success_block;

    // Step 1: execute the external call itself so its side effects land
    // in the SSA.  A revert inside the callee emits `assume(false)` which
    // prunes this arm, leaving the catch arm feasible.
    if (stmt.contains("externalCall"))
    {
      exprt call_expr;
      if (get_expr(stmt["externalCall"], call_expr))
        return true;
      convert_expression_to_code(call_expr);
      success_block.copy_to_operands(call_expr);
    }

    // Step 2: declare return parameters with nondet initial values
    if (success_clause.contains("parameters") &&
        success_clause["parameters"].contains("parameters"))
    {
      for (const auto &param :
           success_clause["parameters"]["parameters"])
      {
        // Use get_var_decl to declare the variable in the symbol table
        exprt var_decl;
        if (get_var_decl(param, var_decl))
          return true;

        // The variable was declared; now assign it a nondet value
        // matching its type
        if (var_decl.is_code() && var_decl.statement() == "decl")
        {
          const symbolt &sym =
            *context.find_symbol(var_decl.op0().identifier());
          symbol_exprt sym_expr(sym.id, sym.type);

          exprt nondet_val;
          get_nondet_expr(sym.type, nondet_val);

          code_assignt assign(sym_expr, nondet_val);
          assign.location() = loc;

          success_block.copy_to_operands(var_decl);
          success_block.copy_to_operands(assign);
        }
        else
        {
          success_block.copy_to_operands(var_decl);
        }
      }
    }

    // Step 3: convert the user-written success block body
    exprt success_body;
    if (get_block(success_clause["block"], success_body))
      return true;
    convert_expression_to_code(success_body);
    success_block.copy_to_operands(success_body);

    // --- catch branch(es) (remaining clauses) ---
    exprt catch_expr;
    if (clauses.size() == 2)
    {
      // Single catch clause
      const auto &cc = clauses[1];

      // Declare catch parameters if present (e.g. Error(string memory reason))
      code_blockt catch_block;
      if (cc.contains("parameters") &&
          cc["parameters"].contains("parameters"))
      {
        for (const auto &param : cc["parameters"]["parameters"])
        {
          exprt var_decl;
          if (get_var_decl(param, var_decl))
            return true;

          if (var_decl.is_code() && var_decl.statement() == "decl")
          {
            const symbolt &sym =
              *context.find_symbol(var_decl.op0().identifier());
            symbol_exprt sym_expr(sym.id, sym.type);
            exprt nondet_val;
            get_nondet_expr(sym.type, nondet_val);
            code_assignt assign(sym_expr, nondet_val);
            assign.location() = loc;
            catch_block.copy_to_operands(var_decl);
            catch_block.copy_to_operands(assign);
          }
          else
          {
            catch_block.copy_to_operands(var_decl);
          }
        }
      }

      exprt catch_body;
      if (get_block(cc["block"], catch_body))
        return true;
      convert_expression_to_code(catch_body);
      catch_block.copy_to_operands(catch_body);
      catch_expr = catch_block;
    }
    else
    {
      // Multiple catch clauses: chain with nondet_bool
      // Build right-to-left: last clause is the final else
      const auto &last_cc = clauses[clauses.size() - 1];
      code_blockt last_block;
      if (last_cc.contains("parameters") &&
          last_cc["parameters"].contains("parameters"))
      {
        for (const auto &param : last_cc["parameters"]["parameters"])
        {
          exprt var_decl;
          if (get_var_decl(param, var_decl))
            return true;
          last_block.copy_to_operands(var_decl);
        }
      }
      exprt last_body;
      if (get_block(last_cc["block"], last_body))
        return true;
      convert_expression_to_code(last_body);
      last_block.copy_to_operands(last_body);

      catch_expr = last_block;

      // Build if-else chain from second-to-last back to first catch clause
      for (int i = static_cast<int>(clauses.size()) - 2; i >= 1; --i)
      {
        const auto &cc = clauses[i];
        code_blockt clause_block;
        if (cc.contains("parameters") &&
            cc["parameters"].contains("parameters"))
        {
          for (const auto &param : cc["parameters"]["parameters"])
          {
            exprt var_decl;
            if (get_var_decl(param, var_decl))
              return true;
            clause_block.copy_to_operands(var_decl);
          }
        }
        exprt clause_body;
        if (get_block(cc["block"], clause_body))
          return true;
        convert_expression_to_code(clause_body);
        clause_block.copy_to_operands(clause_body);

        codet if_catch("ifthenelse");
        if_catch.copy_to_operands(nondet_bool_expr, clause_block, catch_expr);
        if_catch.location() = loc;
        catch_expr = if_catch;
      }
    }

    convert_expression_to_code(catch_expr);

    // Build top-level: if (nondet_bool()) { success } else { catch }
    codet try_if("ifthenelse");
    try_if.copy_to_operands(nondet_bool_expr, success_block, catch_expr);
    try_if.location() = loc;

    new_expr = try_if;
    break;
  }
  case SolidityGrammar::StatementT::InlineAssemblyStatement:
  {
    // T2.4 — gradual Yul lowering for inline assembly.
    //
    // Strategy: try precise lowering of the YulBlock against a fixed
    // supported subset (let / := / pure-256-bit builtins / if / switch /
    // for / break / continue / sload(X.slot) / sstore(X.slot, v) on
    // scalar state vars / nested blocks / number+bool literals).
    // All-or-nothing per block: if any unsupported Yul construct
    // (mload/mstore/calldata*/keccak256/call/return/revert/
    // EVM-intrinsics/Yul-fn-defs/leave/.offset/computed-slot
    // sload/sstore/non-scalar-state-var sload/sstore/multi-LHS/
    // hex-or-string-literals) appears anywhere in the block, fall through
    // to the legacy havoc fallback below.  This guarantees the precise
    // portion can never observe a write the havoc'd portion would have made.
    {
      std::string unsupported_kind, unsupported_src;
      exprt precise;
      if (try_lower_yul_block_precise(
            stmt, loc, precise, unsupported_kind, unsupported_src))
      {
        new_expr = precise;
        break;
      }
      // [APPROX: OVER] Block contains an unsupported Yul construct; emit
      // a single warning naming the offending node + location so users can
      // see why an assertion that depends on this block became a havoc.
      log_warning(
        "[approx] inline assembly at {}:{}: over-approximating - "
        "unsupported Yul construct '{}' ({}); supported subset: "
        "let / := / arithmetic+bitwise+shift / lt/gt/eq/slt/sgt/iszero / "
        "and/or/xor/not / shl/shr / if / switch / for / break / continue / "
        "sload(X.slot) / sstore(X.slot, v) on scalar state vars",
        loc.get_file().c_str(),
        loc.get_line().c_str(),
        unsupported_kind,
        unsupported_src);
    }

    code_blockt havoc_block;

    if (stmt.contains("externalReferences") &&
        stmt["externalReferences"].is_array())
    {
      // Collect unique declaration IDs (a variable may appear multiple times)
      std::set<int> seen_decls;
      for (const auto &ref : stmt["externalReferences"])
      {
        if (!ref.contains("declaration"))
          continue;
        int decl_id = ref["declaration"].get<int>();
        if (!seen_decls.insert(decl_id).second)
          continue; // already processed

        // Skip .slot/.offset references — we'll havoc the variable itself
        if (ref.contains("isSlot") && ref["isSlot"].get<bool>())
          continue;
        if (ref.contains("isOffset") && ref["isOffset"].get<bool>())
          continue;

        const nlohmann::json &decl = find_decl_ref(decl_id);
        if (decl.empty() || decl["nodeType"] != "VariableDeclaration")
          continue;

        // Resolve the variable to a symbol expression
        bool is_state = decl.contains("stateVariable") &&
                        decl["stateVariable"].get<bool>();
        exprt var_expr;
        if (get_var_decl_ref(decl, is_state, var_expr))
          continue; // best-effort: skip if resolution fails

        // Assign nondet value
        exprt nondet_val;
        get_nondet_expr(var_expr.type(), nondet_val);
        code_assignt assign(var_expr, nondet_val);
        assign.location() = loc;
        havoc_block.copy_to_operands(assign);
      }

      // Also havoc variables referenced via .slot (state variables modified
      // through sstore). Find their declaration and havoc the variable.
      for (const auto &ref : stmt["externalReferences"])
      {
        if (!ref.contains("declaration"))
          continue;
        bool is_slot =
          ref.contains("isSlot") && ref["isSlot"].get<bool>();
        if (!is_slot)
          continue;

        int decl_id = ref["declaration"].get<int>();
        if (seen_decls.count(decl_id))
          continue; // already havoc'd above
        seen_decls.insert(decl_id);

        const nlohmann::json &decl = find_decl_ref(decl_id);
        if (decl.empty() || decl["nodeType"] != "VariableDeclaration")
          continue;

        exprt var_expr;
        if (get_var_decl_ref(decl, true, var_expr))
          continue;

        exprt nondet_val;
        get_nondet_expr(var_expr.type(), nondet_val);
        code_assignt assign(var_expr, nondet_val);
        assign.location() = loc;
        havoc_block.copy_to_operands(assign);
      }
    }

    if (havoc_block.operands().empty())
    {
      // No external references — assembly only touches internal EVM state.
      // Generate a skip (no-op).
      new_expr = code_skipt();
    }
    else
    {
      new_expr = havoc_block;
    }
    break;
  }
  case SolidityGrammar::StatementT::StatementTError:
  default:
  {
    log_error(
      "Unimplemented Statement type in rule statement. Got {}",
      SolidityGrammar::statement_to_str(type));
    return true;
  }
  }

  log_debug(
    "solidity", "finish statement {}", SolidityGrammar::statement_to_str(type));
  new_expr.location() = loc;
  return false;
}

// ============================================================================
// T2.4 — Yul precise lowering for inline assembly blocks.
//
// Translates a supported subset of Yul into ESBMC IR with full precision.
// Supported subset:
//   - Statements:  YulBlock, YulVariableDeclaration, YulAssignment, YulIf,
//                  YulSwitch, YulForLoop, YulBreak, YulContinue,
//                  YulExpressionStatement (only sstore at statement level)
//   - Expressions: YulIdentifier, YulLiteral (number/bool), YulFunctionCall
//                  with a whitelisted builtin name
//   - Builtins:    add sub mul div mod addmod mulmod
//                  lt gt slt sgt eq iszero
//                  and or xor not
//                  shl shr
//                  sload  (only sload(X.slot) on scalar state vars)
//                  sstore (only sstore(X.slot, v) on scalar state vars)
//
// Unsupported (block falls back to havoc with a single warning):
//   - Memory/calldata/hashing/calls/returns/reverts/EVM intrinsics
//   - Yul function definitions, leave
//   - Multi-LHS YulAssignment / YulVariableDeclaration
//   - YulLiteral kinds other than number / bool
//   - YulCase with a non-literal selector
//   - .offset external references (no intra-slot packing model)
//   - Computed-slot sload/sstore (sload(0), sload(add(X.slot, 1)),
//     sload(keccak256(...)), etc.)
//   - sload/sstore on non-scalar state vars (struct/mapping/array/bytes/
//     contract-typed)
//
// All-or-nothing rule: pre-flight scans the entire YulBlock; the precise
// lowerer either translates everything or refuses (returns false), and the
// caller emits one warning + falls through to the existing havoc fallback.
// This is the soundness lever — a partial precise translation could miss
// writes the unsupported portion would make.
// ============================================================================

namespace
{
bool is_supported_yul_builtin(const std::string &name)
{
  static const std::set<std::string> ok = {
    "add",     "sub",   "mul",    "div", "mod", "addmod", "mulmod",
    "lt",      "gt",    "slt",    "sgt", "eq",  "iszero",
    "and",     "or",    "xor",    "not",
    "shl",     "shr",
    // sload/sstore are only valid with a YulIdentifier `X.slot` argument
    // resolving to a scalar state variable; the sload/sstore lowering
    // paths in convert_yul_expression / convert_yul_statement enforce
    // those constraints at lowering time (the pre-flight scan does not
    // have access to externalReferences).
    "sload",   "sstore"};
  return ok.count(name) != 0;
}
} // namespace

bool solidity_convertert::yul_node_is_supported(
  const nlohmann::json &node,
  std::string &unsupported_kind,
  std::string &unsupported_src)
{
  if (!node.is_object())
    return true;
  const std::string nt = node.value("nodeType", "");

  if (nt == "YulBlock")
  {
    if (!node.contains("statements") || !node["statements"].is_array())
      return true;
    for (const auto &s : node["statements"])
      if (!yul_node_is_supported(s, unsupported_kind, unsupported_src))
        return false;
    return true;
  }
  if (nt == "YulVariableDeclaration")
  {
    if (
      !node.contains("variables") || !node["variables"].is_array() ||
      node["variables"].size() != 1)
    {
      unsupported_kind = "YulVariableDeclaration:multi-LHS";
      unsupported_src = node.value("src", "");
      return false;
    }
    if (node.contains("value") && node["value"].is_object())
      return yul_node_is_supported(
        node["value"], unsupported_kind, unsupported_src);
    return true;
  }
  if (nt == "YulAssignment")
  {
    if (
      !node.contains("variableNames") || !node["variableNames"].is_array() ||
      node["variableNames"].size() != 1)
    {
      unsupported_kind = "YulAssignment:multi-LHS";
      unsupported_src = node.value("src", "");
      return false;
    }
    if (!node.contains("value") || !node["value"].is_object())
    {
      unsupported_kind = "YulAssignment:no-value";
      unsupported_src = node.value("src", "");
      return false;
    }
    return yul_node_is_supported(
      node["value"], unsupported_kind, unsupported_src);
  }
  if (nt == "YulIf")
  {
    if (
      node.contains("condition") &&
      !yul_node_is_supported(
        node["condition"], unsupported_kind, unsupported_src))
      return false;
    if (
      node.contains("body") &&
      !yul_node_is_supported(node["body"], unsupported_kind, unsupported_src))
      return false;
    return true;
  }
  if (nt == "YulSwitch")
  {
    if (
      node.contains("expression") &&
      !yul_node_is_supported(
        node["expression"], unsupported_kind, unsupported_src))
      return false;
    if (!node.contains("cases") || !node["cases"].is_array())
    {
      unsupported_kind = "YulSwitch:no-cases";
      unsupported_src = node.value("src", "");
      return false;
    }
    for (const auto &c : node["cases"])
    {
      // Default-case selector is either null (older solc) or the string
      // "default" (newer solc); non-default must be a YulLiteral.
      bool is_default = false;
      if (c.contains("value"))
      {
        const auto &v = c["value"];
        if (v.is_null())
          is_default = true;
        else if (v.is_string() && v.get<std::string>() == "default")
          is_default = true;
        else if (v.is_object())
        {
          if (v.value("nodeType", "") != "YulLiteral")
          {
            unsupported_kind = "YulCase:non-literal-selector";
            unsupported_src = c.value("src", "");
            return false;
          }
          if (!yul_node_is_supported(v, unsupported_kind, unsupported_src))
            return false;
        }
        else
        {
          unsupported_kind = "YulCase:unknown-selector";
          unsupported_src = c.value("src", "");
          return false;
        }
      }
      (void)is_default;
      if (
        c.contains("body") &&
        !yul_node_is_supported(c["body"], unsupported_kind, unsupported_src))
        return false;
    }
    return true;
  }
  if (nt == "YulForLoop" || nt == "YulFor")
  {
    for (const char *k : {"pre", "condition", "post", "body"})
    {
      if (
        node.contains(k) &&
        !yul_node_is_supported(node[k], unsupported_kind, unsupported_src))
        return false;
    }
    return true;
  }
  if (nt == "YulFunctionCall")
  {
    const std::string fname =
      node.value("functionName", nlohmann::json::object()).value("name", "");
    if (!is_supported_yul_builtin(fname))
    {
      unsupported_kind = "YulFunctionCall:" + fname;
      unsupported_src = node.value("src", "");
      return false;
    }
    if (node.contains("arguments") && node["arguments"].is_array())
      for (const auto &a : node["arguments"])
        if (!yul_node_is_supported(a, unsupported_kind, unsupported_src))
          return false;
    return true;
  }
  if (nt == "YulIdentifier")
    return true;
  if (nt == "YulLiteral")
  {
    const std::string kind = node.value("kind", "");
    if (kind == "number" || kind == "bool")
      return true;
    unsupported_kind = "YulLiteral:" + kind;
    unsupported_src = node.value("src", "");
    return false;
  }
  if (nt == "YulBreak" || nt == "YulContinue")
    return true;

  if (nt == "YulExpressionStatement")
  {
    // Wraps a side-effecting Yul expression at statement position.  In the
    // supported subset this is exclusively `sstore(X.slot, v)`; the
    // statement-level lowering path enforces the sstore-only restriction
    // and slot-arg validation.  Recurse into the expression so the scan
    // catches an unsupported builtin (e.g. `mstore`) up-front.
    if (node.contains("expression") && node["expression"].is_object())
      return yul_node_is_supported(
        node["expression"], unsupported_kind, unsupported_src);
    unsupported_kind = "YulExpressionStatement:no-expression";
    unsupported_src = node.value("src", "");
    return false;
  }

  // Unknown / unsupported nodeType — typically YulFunctionDefinition,
  // YulLeave, YulTypedName appearing where a statement is expected, etc.
  unsupported_kind = nt.empty() ? "YulNode:unknown" : nt;
  unsupported_src = node.value("src", "");
  return false;
}

bool solidity_convertert::make_yul_local(
  const std::string &asm_id,
  int seq,
  const std::string &yul_name,
  const locationt &loc,
  exprt &out_sym)
{
  std::string name =
    "_yul_" + asm_id + "_" + std::to_string(seq) + "_" + yul_name;
  std::string id;
  if (current_baseContractName.empty() || current_functionName.empty())
  {
    // Free-function context: scope to the function only.
    id = "sol:@F@" +
         (current_functionName.empty() ? std::string("_anon_")
                                       : current_functionName) +
         "@" + name;
  }
  else
  {
    id = "sol:@C@" + current_baseContractName + "@F@" + current_functionName +
         "@" + name;
  }
  symbolt s;
  std::string mod = get_modulename_from_path(absolute_path);
  unsignedbv_typet u256(256);
  get_default_symbol(s, mod, u256, name, id, loc);
  s.lvalue = true;
  s.file_local = true;
  s.static_lifetime = false;
  if (move_symbol_to_context(s) == nullptr)
    return true;
  out_sym = symbol_exprt(id, u256);
  out_sym.location() = loc;
  return false;
}

bool solidity_convertert::convert_yul_expression(
  const nlohmann::json &yul_expr,
  const std::map<std::string, int> &src_to_decl,
  const std::map<std::string, int> &slot_refs,
  const std::map<std::string, exprt> &locals,
  const locationt &loc,
  exprt &out)
{
  unsignedbv_typet u256(256);
  const std::string nt = yul_expr.value("nodeType", "");

  if (nt == "YulLiteral")
  {
    const std::string kind = yul_expr.value("kind", "");
    const std::string val = yul_expr.value("value", "0");
    if (kind == "bool")
      out = from_integer(BigInt(val == "true" ? 1 : 0), u256);
    else if (kind == "number")
    {
      BigInt v;
      if (val.size() > 2 && val[0] == '0' && (val[1] == 'x' || val[1] == 'X'))
        v = string2integer(val.substr(2), 16);
      else
        v = string2integer(val, 10);
      out = from_integer(v, u256);
    }
    else
      return true;
    out.location() = loc;
    return false;
  }

  if (nt == "YulIdentifier")
  {
    const std::string name = yul_expr.value("name", "");
    auto lit = locals.find(name);
    if (lit != locals.end())
    {
      out = lit->second;
      return false;
    }
    auto sit = src_to_decl.find(yul_expr.value("src", ""));
    if (sit == src_to_decl.end())
      return true;
    const nlohmann::json &decl = find_decl_ref(sit->second);
    if (decl.empty() || decl.value("nodeType", "") != "VariableDeclaration")
      return true;
    bool is_state =
      decl.contains("stateVariable") && decl["stateVariable"].get<bool>();
    if (get_var_decl_ref(decl, is_state, out))
      return true;
    // Yul reads of internal-fn-ptr-typed locals see 0 (uninit fn-ptr semantics).
    if (out.type().get_bool("#sol_func_ptr"))
      out = from_integer(BigInt(0), u256);
    else
      solidity_gen_typecast(ns, out, u256);
    return false;
  }

  if (nt == "YulFunctionCall")
  {
    const std::string fname =
      yul_expr.value("functionName", nlohmann::json::object())
        .value("name", "");
    const auto &args = yul_expr["arguments"];

    auto eval_arg = [&](size_t i, exprt &dst) -> bool {
      return convert_yul_expression(
        args[i], src_to_decl, slot_refs, locals, loc, dst);
    };
    auto u256_const = [&](const BigInt &v) {
      return from_integer(v, u256);
    };
    auto bool_to_u256 = [&](const exprt &cond) {
      return if_exprt(cond, u256_const(1), u256_const(0));
    };

    // Pure 2-operand uint256 ops: + - *
    if (fname == "add" || fname == "sub" || fname == "mul")
    {
      if (args.size() != 2)
        return true;
      exprt a, b;
      if (eval_arg(0, a) || eval_arg(1, b))
        return true;
      if (fname == "add")
        out = plus_exprt(a, b);
      else if (fname == "sub")
        out = minus_exprt(a, b);
      else
        out = mult_exprt(a, b);
      out.type() = u256;
      out.location() = loc;
      return false;
    }

    // div / mod: lower to plain div_exprt / mod_exprt so goto_check's
    // div-by-zero check fires when the divisor is symbolically reachable
    // as 0. EVM/Yul defines `div(_, 0) == 0` (no panic, no revert), but
    // this is almost always a bug pattern in user code (programmer
    // expected Solidity 0.8+ panic semantics). With default checks
    // enabled, ESBMC reports "division by zero" violations on reachable
    // zero divisors — which is the verification-useful behavior. With
    // `--no-div-by-zero-check`, the check is suppressed and the model
    // returns SMT-LIB's bvudiv-zero result (all-1s), NOT Yul's 0 — see
    // approximation-ledger.md row #1 for this soundness gap.
    if (fname == "div" || fname == "mod")
    {
      if (args.size() != 2)
        return true;
      exprt a, b;
      if (eval_arg(0, a) || eval_arg(1, b))
        return true;
      if (fname == "div")
        out = div_exprt(a, b);
      else
        out = mod_exprt(a, b);
      out.type() = u256;
      out.location() = loc;
      return false;
    }

    // addmod / mulmod: same div-by-zero exposure for the modulus arg.
    // (Note: the inner add/mul still uses 2^256-wrap arithmetic, not the
    // EVM-spec arbitrary-precision intermediate — that's a separate T2.4
    // gap, not addressed here.)
    if (fname == "addmod" || fname == "mulmod")
    {
      if (args.size() != 3)
        return true;
      exprt a, b, m;
      if (eval_arg(0, a) || eval_arg(1, b) || eval_arg(2, m))
        return true;
      exprt inner;
      if (fname == "addmod")
        inner = plus_exprt(a, b);
      else
        inner = mult_exprt(a, b);
      inner.type() = u256;
      out = mod_exprt(inner, m);
      out.type() = u256;
      out.location() = loc;
      return false;
    }

    // Unsigned comparisons → uint256 (1 / 0)
    if (fname == "lt" || fname == "gt" || fname == "eq")
    {
      if (args.size() != 2)
        return true;
      exprt a, b;
      if (eval_arg(0, a) || eval_arg(1, b))
        return true;
      exprt cond;
      if (fname == "lt")
        cond = binary_relation_exprt(a, "<", b);
      else if (fname == "gt")
        cond = binary_relation_exprt(a, ">", b);
      else
        cond = equality_exprt(a, b);
      out = bool_to_u256(cond);
      out.type() = u256;
      out.location() = loc;
      return false;
    }

    // Signed comparisons: cast operands to int256 first.
    if (fname == "slt" || fname == "sgt")
    {
      if (args.size() != 2)
        return true;
      exprt a, b;
      if (eval_arg(0, a) || eval_arg(1, b))
        return true;
      signedbv_typet s256(256);
      solidity_gen_typecast(ns, a, s256);
      solidity_gen_typecast(ns, b, s256);
      exprt cond;
      if (fname == "slt")
        cond = binary_relation_exprt(a, "<", b);
      else
        cond = binary_relation_exprt(a, ">", b);
      out = bool_to_u256(cond);
      out.type() = u256;
      out.location() = loc;
      return false;
    }

    if (fname == "iszero")
    {
      if (args.size() != 1)
        return true;
      exprt a;
      if (eval_arg(0, a))
        return true;
      equality_exprt cond(a, u256_const(0));
      out = bool_to_u256(cond);
      out.type() = u256;
      out.location() = loc;
      return false;
    }

    // Bitwise binary
    if (fname == "and" || fname == "or" || fname == "xor")
    {
      if (args.size() != 2)
        return true;
      exprt a, b;
      if (eval_arg(0, a) || eval_arg(1, b))
        return true;
      const char *id =
        (fname == "and") ? "bitand" : (fname == "or") ? "bitor" : "bitxor";
      out = exprt(id, u256);
      out.copy_to_operands(a, b);
      out.location() = loc;
      return false;
    }

    if (fname == "not")
    {
      if (args.size() != 1)
        return true;
      exprt a;
      if (eval_arg(0, a))
        return true;
      out = exprt("bitnot", u256);
      out.copy_to_operands(a);
      out.location() = loc;
      return false;
    }

    // Shifts. Yul argument order: shift amount FIRST, value SECOND.
    // EVM clamps `shift >= 256` to 0 for shl/shr.
    if (fname == "shl" || fname == "shr")
    {
      if (args.size() != 2)
        return true;
      exprt s, v;
      if (eval_arg(0, s) || eval_arg(1, v))
        return true;
      const char *id = (fname == "shl") ? "shl" : "lshr";
      exprt shifted(id, u256);
      shifted.copy_to_operands(v, s);
      binary_relation_exprt in_range(s, "<", u256_const(256));
      out = if_exprt(in_range, shifted, u256_const(0));
      out.type() = u256;
      out.location() = loc;
      return false;
    }

    // sload(X.slot) — read scalar state variable X (widened to uint256).
    // The argument must be a YulIdentifier whose src matches a `.slot`
    // external reference resolving to a scalar state variable.  Any
    // failed gate (computed slot, non-state-var, non-scalar type) returns
    // true to abort precise lowering, so the caller falls through to
    // havoc.  The havoc fallback's existing `.slot`-ref handling
    // (lines 1162-1191 of this file) re-nondets every state var hit via
    // `.slot`, preserving soundness for the rejected block.
    if (fname == "sload")
    {
      if (args.size() != 1)
        return true;
      const nlohmann::json &a0 = args[0];
      if (a0.value("nodeType", "") != "YulIdentifier")
        return true;
      auto sit = slot_refs.find(a0.value("src", ""));
      if (sit == slot_refs.end())
        return true;
      const nlohmann::json &decl = find_decl_ref(sit->second);
      if (decl.empty() || decl.value("nodeType", "") != "VariableDeclaration")
        return true;
      if (!decl.value("stateVariable", false))
        return true;
      exprt var_expr;
      if (get_var_decl_ref(decl, /*is_state=*/true, var_expr))
        return true;
      const irep_idt tid = var_expr.type().id();
      if (tid != "unsignedbv" && tid != "signedbv" && tid != "bool")
        return true;
      out = var_expr;
      solidity_gen_typecast(ns, out, u256);
      out.location() = loc;
      return false;
    }

    return true; // unknown builtin (pre-flight should have rejected)
  }

  return true; // unknown nodeType
}

bool solidity_convertert::convert_yul_statement(
  const nlohmann::json &yul_stmt,
  const std::string &asm_id,
  const std::map<std::string, int> &src_to_decl,
  const std::map<std::string, int> &slot_refs,
  std::map<std::string, exprt> &locals,
  int &local_seq,
  const locationt &loc,
  exprt &out)
{
  unsignedbv_typet u256(256);
  const std::string nt = yul_stmt.value("nodeType", "");

  if (nt == "YulBlock")
    return convert_yul_block(
      yul_stmt, asm_id, src_to_decl, slot_refs, locals, local_seq, loc, out);

  if (nt == "YulVariableDeclaration")
  {
    const std::string yname = yul_stmt["variables"][0].value("name", "");
    if (yname.empty())
      return true;

    exprt sym;
    if (make_yul_local(asm_id, local_seq++, yname, loc, sym))
      return true;
    locals[yname] = sym;

    code_blockt blk;
    code_declt decl(sym);
    decl.location() = loc;
    blk.copy_to_operands(decl);

    exprt rhs;
    if (yul_stmt.contains("value") && yul_stmt["value"].is_object())
    {
      if (convert_yul_expression(
            yul_stmt["value"], src_to_decl, slot_refs, locals, loc, rhs))
        return true;
    }
    else
      rhs = from_integer(BigInt(0), u256);
    solidity_gen_typecast(ns, rhs, u256);
    code_assignt assign(sym, rhs);
    assign.location() = loc;
    blk.copy_to_operands(assign);
    out = blk;
    return false;
  }

  if (nt == "YulAssignment")
  {
    const nlohmann::json &lhs_id = yul_stmt["variableNames"][0];
    const nlohmann::json &rhs_node = yul_stmt["value"];
    const std::string lname = lhs_id.value("name", "");

    // Resolve LHS
    exprt lhs;
    auto lit = locals.find(lname);
    if (lit != locals.end())
      lhs = lit->second;
    else
    {
      auto sit = src_to_decl.find(lhs_id.value("src", ""));
      if (sit == src_to_decl.end())
        return true;
      const nlohmann::json &decl = find_decl_ref(sit->second);
      if (decl.empty() || decl.value("nodeType", "") != "VariableDeclaration")
        return true;
      bool is_state =
        decl.contains("stateVariable") && decl["stateVariable"].get<bool>();
      if (get_var_decl_ref(decl, is_state, lhs))
        return true;
    }

    // Special case: `dst := src` where rhs is a YulIdentifier — preserve the
    // legacy fast-path semantics so bytes-struct / fn-ptr destinations work
    // (they don't roundtrip through uint256).
    if (rhs_node.value("nodeType", "") == "YulIdentifier")
    {
      const std::string rname = rhs_node.value("name", "");
      exprt src_expr;
      auto rit = locals.find(rname);
      if (rit != locals.end())
        src_expr = rit->second;
      else
      {
        auto sit = src_to_decl.find(rhs_node.value("src", ""));
        if (sit == src_to_decl.end())
          return true;
        const nlohmann::json &decl = find_decl_ref(sit->second);
        if (decl.empty() || decl.value("nodeType", "") != "VariableDeclaration")
          return true;
        bool is_state =
          decl.contains("stateVariable") && decl["stateVariable"].get<bool>();
        if (get_var_decl_ref(decl, is_state, src_expr))
          return true;
      }
      // Resolve a type through pointer/symbol indirection down to the
      // underlying struct; returns the resolved symbol tag iff it is a
      // struct, else empty (mirrors struct_type_has_component's idiom;
      // struct_union_typet exposes no plain tag accessor).
      auto struct_tag = [this](const typet &t) -> irep_idt
      {
        typet rt = t;
        if (rt.id() == "pointer")
          rt = rt.subtype();
        irep_idt tag;
        while (rt.id() == "symbol")
        {
          tag = to_symbol_type(rt).get_identifier();
          const symbolt *s = context.find_symbol(tag);
          if (s == nullptr)
            return irep_idt();
          rt = s->type;
        }
        return rt.id() == "struct" ? tag : irep_idt();
      };

      exprt rhs;
      if (src_expr.type().get_bool("#sol_func_ptr"))
        rhs = from_integer(BigInt(0), lhs.type());
      else
      {
        const irep_idt lt = struct_tag(lhs.type());
        const irep_idt rt = struct_tag(src_expr.type());
        if (!lt.empty() && !rt.empty() && lt != rt)
        {
          // [APPROX: OVER] EVM inline-assembly pointer/value reinterpret
          // between two distinct struct layouts (e.g. bytes32 <-> a memory
          // struct) that c_typecastt cannot bridge. A faithful memory-
          // pointer-aliasing model is out of scope; over-approximate by
          // havoc'ing the destination to a nondet of its own declared
          // type so the dependent member access stays well-typed.
          log_warning(
            "[approx] inline assembly at {}:{}: over-approximating "
            "struct/value reinterpret '{} := {}' (struct '{}' := struct "
            "'{}') - '{}' havoc'd to nondet of its declared type; faithful "
            "EVM memory-pointer aliasing is out of scope",
            loc.get_file().c_str(),
            loc.get_line().c_str(),
            lname,
            rname,
            lt,
            rt,
            lname);
          get_nondet_expr(lhs.type(), rhs);
        }
        else
        {
          rhs = src_expr;
          solidity_gen_typecast(ns, rhs, lhs.type());
        }
      }
      code_assignt assign(lhs, rhs);
      assign.location() = loc;
      out = assign;
      return false;
    }

    exprt rhs;
    if (convert_yul_expression(
          rhs_node, src_to_decl, slot_refs, locals, loc, rhs))
      return true;
    solidity_gen_typecast(ns, rhs, lhs.type());
    code_assignt assign(lhs, rhs);
    assign.location() = loc;
    out = assign;
    return false;
  }

  if (nt == "YulIf")
  {
    exprt cond_val;
    if (convert_yul_expression(
          yul_stmt["condition"], src_to_decl, slot_refs, locals, loc, cond_val))
      return true;
    binary_relation_exprt cond_ne(cond_val, "notequal", from_integer(BigInt(0), u256));

    exprt body;
    if (convert_yul_block(
          yul_stmt["body"], asm_id, src_to_decl, slot_refs, locals,
          local_seq, loc, body))
      return true;

    codet if_expr("ifthenelse");
    if_expr.copy_to_operands(cond_ne, body);
    if_expr.location() = loc;
    out = if_expr;
    return false;
  }

  if (nt == "YulSwitch")
  {
    exprt e;
    if (convert_yul_expression(
          yul_stmt["expression"], src_to_decl, slot_refs, locals, loc, e))
      return true;

    const auto &cases = yul_stmt["cases"];

    // Build the default branch first; non-default cases chain right-to-left
    // around it.  solc represents default-case selector as either JSON null
    // (older) or the literal string "default" (newer).
    auto is_default = [](const nlohmann::json &c) -> bool {
      if (!c.contains("value"))
        return true;
      const auto &v = c["value"];
      return v.is_null() || (v.is_string() && v.get<std::string>() == "default");
    };

    exprt tail = code_skipt();
    for (const auto &c : cases)
    {
      if (is_default(c))
      {
        if (convert_yul_block(
              c["body"], asm_id, src_to_decl, slot_refs, locals,
              local_seq, loc, tail))
          return true;
        break;
      }
    }

    std::vector<nlohmann::json> non_default;
    for (const auto &c : cases)
      if (!is_default(c))
        non_default.push_back(c);

    for (auto it = non_default.rbegin(); it != non_default.rend(); ++it)
    {
      exprt key;
      if (convert_yul_expression(
            (*it)["value"], src_to_decl, slot_refs, locals, loc, key))
        return true;
      equality_exprt cond(e, key);
      exprt body;
      if (convert_yul_block(
            (*it)["body"], asm_id, src_to_decl, slot_refs, locals,
            local_seq, loc, body))
        return true;
      codet if_expr("ifthenelse");
      if_expr.copy_to_operands(cond, body, tail);
      if_expr.location() = loc;
      tail = if_expr;
    }

    out = tail;
    return false;
  }

  if (nt == "YulForLoop" || nt == "YulFor")
  {
    // Snapshot locals so `let` declarations in `pre` fall out of scope after
    // the for-loop completes.
    auto snapshot = locals;

    code_blockt outer;

    // Walk pre's statements directly (NOT via convert_yul_block) — Yul's `pre`
    // is not a scoped block; its `let`-bindings must remain in scope for
    // cond/post/body.  The outer for-loop snapshot drops them after the loop.
    if (
      yul_stmt.contains("pre") && yul_stmt["pre"].is_object() &&
      yul_stmt["pre"].value("nodeType", "") == "YulBlock" &&
      yul_stmt["pre"].contains("statements") &&
      yul_stmt["pre"]["statements"].is_array())
    {
      for (const auto &s : yul_stmt["pre"]["statements"])
      {
        exprt s_expr;
        if (convert_yul_statement(
              s, asm_id, src_to_decl, slot_refs, locals, local_seq, loc,
              s_expr))
          return true;
        outer.copy_to_operands(s_expr);
      }
    }

    exprt cond_val;
    if (convert_yul_expression(
          yul_stmt["condition"], src_to_decl, slot_refs, locals, loc, cond_val))
      return true;
    binary_relation_exprt cond_ne(cond_val, "notequal", from_integer(BigInt(0), u256));

    exprt post_expr;
    if (convert_yul_block(
          yul_stmt["post"],
          asm_id,
          src_to_decl,
          slot_refs,
          locals,
          local_seq,
          loc,
          post_expr))
      return true;

    exprt body_expr;
    if (convert_yul_block(
          yul_stmt["body"],
          asm_id,
          src_to_decl,
          slot_refs,
          locals,
          local_seq,
          loc,
          body_expr))
      return true;

    code_fort code_for;
    code_for.init() = code_skipt();
    code_for.cond() = cond_ne;
    code_for.iter() = static_cast<const codet &>(post_expr);
    code_for.body() = static_cast<const codet &>(body_expr);
    code_for.location() = loc;
    outer.copy_to_operands(code_for);
    out = outer;

    locals = snapshot;
    return false;
  }

  if (nt == "YulBreak")
  {
    code_breakt brk;
    brk.location() = loc;
    out = brk;
    return false;
  }

  if (nt == "YulContinue")
  {
    code_continuet cont;
    cont.location() = loc;
    out = cont;
    return false;
  }

  if (nt == "YulExpressionStatement")
  {
    // sstore(X.slot, v) is the only side-effecting expression statement
    // in the supported subset.  Everything else (a bare sload at
    // statement position, an unknown builtin call) returns true to
    // abort precise lowering — caller falls through to havoc.
    if (!yul_stmt.contains("expression") ||
        !yul_stmt["expression"].is_object())
      return true;
    const nlohmann::json &expr = yul_stmt["expression"];
    if (expr.value("nodeType", "") != "YulFunctionCall")
      return true;
    const std::string fname =
      expr.value("functionName", nlohmann::json::object()).value("name", "");
    if (fname != "sstore")
      return true;
    const auto &args = expr["arguments"];
    if (!args.is_array() || args.size() != 2)
      return true;

    // Resolve slot arg → state-variable symbol.  Must be a YulIdentifier
    // whose src matches a `.slot` ext-ref pointing at a scalar state var.
    const nlohmann::json &a0 = args[0];
    if (a0.value("nodeType", "") != "YulIdentifier")
      return true;
    auto sit = slot_refs.find(a0.value("src", ""));
    if (sit == slot_refs.end())
      return true;
    const nlohmann::json &decl = find_decl_ref(sit->second);
    if (decl.empty() || decl.value("nodeType", "") != "VariableDeclaration")
      return true;
    if (!decl.value("stateVariable", false))
      return true;
    exprt lhs;
    if (get_var_decl_ref(decl, /*is_state=*/true, lhs))
      return true;
    const irep_idt tid = lhs.type().id();
    if (tid != "unsignedbv" && tid != "signedbv" && tid != "bool")
      return true;

    // Lower the value expression and narrow to the state var's native type.
    exprt rhs;
    if (convert_yul_expression(
          args[1], src_to_decl, slot_refs, locals, loc, rhs))
      return true;
    solidity_gen_typecast(ns, rhs, lhs.type());

    code_assignt assign(lhs, rhs);
    assign.location() = loc;
    out = assign;
    return false;
  }

  return true; // unknown nodeType (pre-flight should have caught it)
}

bool solidity_convertert::convert_yul_block(
  const nlohmann::json &yul_block,
  const std::string &asm_id,
  const std::map<std::string, int> &src_to_decl,
  const std::map<std::string, int> &slot_refs,
  std::map<std::string, exprt> &locals,
  int &local_seq,
  const locationt &loc,
  exprt &out)
{
  if (!yul_block.is_object() || yul_block.value("nodeType", "") != "YulBlock")
    return true;

  // Snapshot for nested-scope shadowing.
  auto snapshot = locals;

  code_blockt blk;
  if (yul_block.contains("statements") && yul_block["statements"].is_array())
  {
    for (const auto &s : yul_block["statements"])
    {
      exprt s_expr;
      if (convert_yul_statement(
            s, asm_id, src_to_decl, slot_refs, locals, local_seq, loc,
            s_expr))
        return true;
      blk.copy_to_operands(s_expr);
    }
  }

  out = blk;
  out.location() = loc;
  locals = snapshot;
  return false;
}

bool solidity_convertert::try_lower_yul_block_precise(
  const nlohmann::json &asm_stmt,
  const locationt &loc,
  exprt &out,
  std::string &unsupported_kind,
  std::string &unsupported_src)
{
  if (!asm_stmt.contains("AST") || !asm_stmt["AST"].is_object())
  {
    unsupported_kind = "InlineAssembly:no-AST";
    unsupported_src = asm_stmt.value("src", "");
    return false;
  }
  const nlohmann::json &yul_root = asm_stmt["AST"];

  // Pre-flight scan — all-or-nothing rule.
  if (!yul_node_is_supported(yul_root, unsupported_kind, unsupported_src))
    return false;

  // Build two src-range -> declaration-id maps for outer-scope identifier
  // lookup: `src_to_decl` for plain (non-suffixed) external references,
  // `slot_refs` for `.slot` references consumed by sload/sstore.
  // `.offset` references imply intra-slot packing — we have no model for
  // that, so reject the entire block on `.offset`.
  std::map<std::string, int> src_to_decl;
  std::map<std::string, int> slot_refs;
  if (
    asm_stmt.contains("externalReferences") &&
    asm_stmt["externalReferences"].is_array())
  {
    for (const auto &ref : asm_stmt["externalReferences"])
    {
      const bool is_slot =
        ref.contains("isSlot") && ref["isSlot"].get<bool>();
      const bool is_offset =
        ref.contains("isOffset") && ref["isOffset"].get<bool>();
      if (is_offset)
      {
        unsupported_kind = "ExternalReference:offset";
        unsupported_src = ref.value("src", "");
        return false;
      }
      if (
        !ref.contains("declaration") || !ref["declaration"].is_number() ||
        !ref.contains("src") || !ref["src"].is_string())
        continue;
      const std::string src_key = ref["src"].get<std::string>();
      const int decl_id = ref["declaration"].get<int>();
      if (is_slot)
        slot_refs[src_key] = decl_id;
      else
        src_to_decl[src_key] = decl_id;
    }
  }

  std::string asm_id = "asm" + std::to_string(asm_stmt.value("id", 0));
  std::map<std::string, exprt> locals;
  int local_seq = 0;

  if (convert_yul_block(
        yul_root, asm_id, src_to_decl, slot_refs, locals, local_seq, loc,
        out))
  {
    unsupported_kind = "convert_failure";
    unsupported_src = asm_stmt.value("src", "");
    return false;
  }

  return true;
}
