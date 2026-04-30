/// \file solidity_convert_ref.cpp
/// \brief Reference and symbol resolution for the Solidity frontend.
///
/// Handles resolution of symbol references, declaration references, and
/// function declaration references in the solc JSON AST. Looks up symbols
/// in ESBMC's context (symbol table), resolves cross-contract references
/// via the AST's referencedDeclaration IDs, and creates the corresponding
/// irep2 symbol expressions.

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

namespace
{
/* Peel typecast wrappers emitted by `solidity_gen_typecast` around the
 * library call that evaluates `m[k]` for `mapping(K => T[])` (see
 * `solidity_convert_mapping.cpp::get_new_mapping_index_access`'s
 * DYNARRAY dispatch). Returns a pointer into the original expression
 * tree; do not free. */
static const exprt *peel_typecasts(const exprt &e)
{
  const exprt *p = &e;
  while (p->id() == "typecast" && !p->operands().empty())
    p = &p->op0();
  return p;
}

/* Return true iff `base` is (a typecast around) a `map_dynarr_get` call. */
static bool is_map_dynarr_get_base(const exprt &base)
{
  const exprt *inner = peel_typecasts(base);
  if (inner->id() != "sideeffect")
    return false;
  if (to_side_effect_expr(*inner).get_statement() != "function_call")
    return false;
  if (inner->operands().empty())
    return false;
  const exprt &fn = inner->op0();
  return fn.identifier().as_string() == "c:@F@map_dynarr_get";
}

/* Pull out the underlying `map_dynarr_get` call from `base`.
 * Caller must have verified via `is_map_dynarr_get_base`. */
static const side_effect_expr_function_callt &
find_map_dynarr_get_call(const exprt &base)
{
  const exprt *inner = peel_typecasts(base);
  assert(inner->id() == "sideeffect");
  return static_cast<const side_effect_expr_function_callt &>(*inner);
}
} // anonymous namespace

void solidity_convertert::get_symbol_decl_ref(
  const std::string &sym_name,
  const std::string &sym_id,
  const typet &t,
  exprt &new_expr)
{
  if (context.find_symbol(sym_id) != nullptr)
    new_expr = symbol_expr(*context.find_symbol(sym_id));
  else
  {
    new_expr = exprt("symbol", t);
    new_expr.identifier(sym_id);
    new_expr.name(sym_name);
    new_expr.pretty_name(sym_name);
  }
}

/**
  This function can return expr with either id::symbol or id::member
  id::memebr can only be the case where this.xx
  @decl: declaration json node
  @is_this_ptr: whether we need to convert x => this.x
  @cname: based contract name
*/
bool solidity_convertert::get_var_decl_ref(
  const nlohmann::json &decl,
  const bool is_this_ptr,
  exprt &new_expr)
{
  // Function to configure new_expr that has a +ve referenced id, referring to a variable declaration
  assert(decl["nodeType"] == "VariableDeclaration");
  std::string name, id;
  if (get_var_decl_name(decl, name, id))
    return true;

  typet type;
  if (get_type_description(decl, decl["typeName"]["typeDescriptions"], type))
    return true;

  bool is_dynarray_state_var =
    get_sol_type(type) == SolidityGrammar::SolType::DYNARRAY &&
    decl.contains("stateVariable") && decl["stateVariable"].get<bool>();
  bool is_global_static_mapping =
    (get_sol_type(type) == SolidityGrammar::SolType::MAPPING &&
     type.is_array()) ||
    type.get_bool("#sol_mapping_array") || is_dynarray_state_var;

  if (context.find_symbol(id) != nullptr)
    new_expr = symbol_expr(*context.find_symbol(id));
  else
  {
    // solidity allows something like:
    // uint8[2] y = x;
    // uint8[2] x = [1, 2];
    // in state variable level
    bool is_state_var = decl["stateVariable"].get<bool>();
    if (is_state_var && is_this_ptr)
    {
      exprt decls;
      if (get_var_decl(decl, decls))
        return true;
      new_expr = symbol_expr(*context.find_symbol(id));
    }
    else
    {
      // variable with no value
      new_expr = exprt("symbol", type);
      new_expr.identifier(id);
      new_expr.name(name);
      new_expr.pretty_name(name);
    }
  }

  if (is_this_ptr && !is_global_static_mapping)
  {
    if (decl["stateVariable"])
    {
      // check if it's a constant in the library,
      // if so, no need to add the this pointer
      std::string c_name;
      get_current_contract_name(decl, c_name);
      if (
        !c_name.empty() &&
        std::find(contractNamesList.begin(), contractNamesList.end(), c_name) ==
          contractNamesList.end())
      {
        assert(decl["mutability"] == "constant");
        return false;
      }

      // this means we are parsing function body
      // and the variable is a state var
      // data = _data ==> this->data = _data;

      // get function this pointer
      exprt this_ptr;
      if (current_functionDecl)
      {
        if (get_func_decl_this_ref(*current_functionDecl, this_ptr))
          return true;
      }
      else
      {
        if (get_ctor_decl_this_ref(c_name, this_ptr))
          return true;
      }

      // construct member access this->data
      assert(!new_expr.name().empty());
      new_expr = member_exprt(this_ptr, new_expr.name(), new_expr.type());
    }
  }
  return false;
}

/*
  we got two get_func_decl_ref_type,
  this one is to get the expr
*/
bool solidity_convertert::get_func_decl_ref(
  const nlohmann::json &decl,
  exprt &new_expr)
{
  // Function to configure new_expr that has a +ve referenced id, referring to a function declaration
  // This allow to get func symbol before we add it to the symbol table
  assert(
    decl["nodeType"] == "FunctionDefinition" ||
    decl["nodeType"] == "EventDefinition" ||
    decl["nodeType"] == "ErrorDefinition");

  std::string name, id;
  get_function_definition_name(decl, name, id);

  if (context.find_symbol(id) != nullptr)
  {
    new_expr = symbol_expr(*context.find_symbol(id));
    return false;
  }

  typet type;
  if (get_func_decl_ref_type(
        decl, type)) // "type-name" as in state-variable-declaration
    return true;

  //! function with no value i.e function body
  new_expr = exprt("symbol", type);
  new_expr.identifier(id);
  new_expr.name(name);
  return false;
}

/*
  we got two get_func_decl_ref_type,
  this one is to get the json
  return empty_json if it's not found
*/
const nlohmann::json &solidity_convertert::get_func_decl_ref(
  const std::string &c_name,
  const std::string &f_name)
{
  nlohmann::json &nodes = src_ast_json["nodes"];
  for (nlohmann::json::iterator itr = nodes.begin(); itr != nodes.end(); ++itr)
  {
    if ((*itr)["nodeType"] == "ContractDefinition" && (*itr)["name"] == c_name)
    {
      nlohmann::json &ast_nodes = (*itr)["nodes"];
      for (nlohmann::json::iterator itrr = ast_nodes.begin();
           itrr != ast_nodes.end();
           ++itrr)
      {
        if ((*itrr)["nodeType"] == "FunctionDefinition")
        {
          if ((*itrr).contains("name") && (*itrr)["name"] == f_name)
            return (*itrr);
          if ((*itrr).contains("kind") && (*itrr)["kind"] == f_name)
            return (*itrr);
        }
      }
    }
  }

  return empty_json;
}

// wrapper
bool solidity_convertert::get_func_decl_this_ref(
  const nlohmann::json &decl,
  exprt &new_expr)
{
  assert(
    !decl.empty() &&
    !decl.is_null()); // yet we cannot detect if it's a Dangling
  std::string func_name, func_id;
  get_function_definition_name(decl, func_name, func_id);
  std::string current_contractName;
  get_current_contract_name(decl, current_contractName);
  if (current_contractName.empty())
  {
    log_error("failed to obtain current contract name");
    return true;
  }

  return get_func_decl_this_ref(current_contractName, func_id, new_expr);
}

// get the this pointer symbol
bool solidity_convertert::get_func_decl_this_ref(
  const std::string contract_name,
  const std::string &func_id,
  exprt &new_expr)
{
  log_debug(
    "solidity",
    "\t@@@ get this reference of func {} in contract {}",
    func_id,
    contract_name);
  std::string this_id = func_id + "#this";
  locationt l;
  code_typet type;
  type.return_type() = empty_typet();
  type.return_type().set("cpp_type", "void");

  if (context.find_symbol(this_id) == nullptr)
  {
    std::string debug_modulename = get_modulename_from_path(absolute_path);
    get_function_this_pointer_param(
      contract_name, func_id, debug_modulename, l, type);
  }

  assert(context.find_symbol(this_id) != nullptr);
  new_expr = symbol_expr(*context.find_symbol(this_id));
  return false;
}

bool solidity_convertert::get_enum_member_ref(
  const nlohmann::json &decl,
  exprt &new_expr)
{
  assert(decl["nodeType"] == "EnumValue");

  // The integer value lives in `Value`, but only when add_enum_member_val
  // has been run on the parent EnumDefinition. That preprocessing is
  // currently driven from get_contract_definition's per-contract loop and
  // never fires for enums declared inside an interface (or any other
  // contract whose name doesn't match the current verification target),
  // so cross-contract references like `I.Direction.Right` from a sibling
  // library/contract used to crash with a json type_error here. Compute
  // the value lazily by walking back to the enclosing EnumDefinition and
  // taking the member's index — that's exactly what add_enum_member_val
  // would have written.
  std::string rhs;
  if (decl.contains("Value"))
    rhs = decl["Value"].get<std::string>();
  else
  {
    const int member_id = decl["id"].get<int>();
    bool found = false;
    std::function<void(const nlohmann::json &)> scan;
    scan = [&](const nlohmann::json &n) {
      if (found)
        return;
      if (n.is_object())
      {
        if (
          n.value("nodeType", "") == "EnumDefinition" &&
          n.contains("members") && n["members"].is_array())
        {
          int idx = 0;
          for (const auto &m : n["members"])
          {
            if (m.value("id", 0) == member_id)
            {
              rhs = std::to_string(idx);
              found = true;
              return;
            }
            ++idx;
          }
        }
        for (const auto &kv : n.items())
          scan(kv.value());
      }
      else if (n.is_array())
      {
        for (const auto &e : n)
          scan(e);
      }
    };
    scan(src_ast_json);
    if (!found)
    {
      log_error(
        "get_enum_member_ref: cannot resolve enum member id {} to a value",
        member_id);
      return true;
    }
  }

  new_expr = constant_exprt(
    integer2binary(string2integer(rhs), bv_width(int_type())), rhs, int_type());

  return false;
}

// get the esbmc built-in methods
bool solidity_convertert::get_esbmc_builtin_ref(
  const nlohmann::json &decl,
  exprt &new_expr)
{
  log_debug("solidity", "\t@@@ get_esbmc_builtin_ref");
  // Function to configure new_expr that has a -ve referenced id
  // -ve ref id means built-in functions or variables.
  // Add more special function names here
  if (
    decl.contains("referencedDeclaration") &&
    !decl["referencedDeclaration"].is_null() &&
    decl["referencedDeclaration"].get<int>() >= 0)
    return true;

  if (!decl.contains("name"))
    return get_sol_builtin_ref(decl, new_expr);

  const std::string blt_name = decl["name"].get<std::string>();
  std::string name, id;

  // "require" keyword is virtually identical to "assume"
  if (
    blt_name == "require" || blt_name == "revert" ||
    blt_name == "__ESBMC_assume" || blt_name == "__VERIFIER_assume")
    name = "__ESBMC_assume";
  else if (
    blt_name == "assert" || blt_name == "__ESBMC_assert" ||
    blt_name == "__VERIFIER_assert")
    name = "assert";
  else
    //!assume it's a solidity built-in func
    return get_sol_builtin_ref(decl, new_expr);
  id = "c:@F@" + name;

  if (name == "__ESBMC_assume")
  {
    assert(context.find_symbol(id) != nullptr);
    new_expr = symbol_expr(*context.find_symbol(id));
    new_expr.type().set("#sol_name", blt_name);
  }
  else
  {
    // assert
    typet type;
    code_typet convert_type;
    typet return_type;
    return_type = bool_t;
    convert_type.return_type() = return_type;
    type = convert_type;
    type.set("#sol_name", blt_name);

    new_expr = exprt("symbol", type);
    new_expr.identifier(id);
    new_expr.name(name);

    locationt loc;
    get_location_from_node(decl, loc);
    new_expr.location() = loc;
    if (current_functionDecl)
      new_expr.location().function(current_functionName);
  }

  return false;
}

/*
  check if it's a solidity built-in function
  - if so, get the function definition reference, assign to new_expr and return false
  - if not, return true
*/
bool solidity_convertert::get_sol_builtin_ref(
  const nlohmann::json expr,
  exprt &new_expr)
{
  log_debug(
    "solidity",
    "\t@@@ expecting solidity builtin ref, got nodeType={}",
    expr["nodeType"].get<std::string>());
  // get the reference from the pre-populated symbol table
  // note that this could be either vars or funcs.
  assert(expr.contains("nodeType"));
  locationt l;
  get_location_from_node(expr, l);

  if (expr["nodeType"].get<std::string>() == "FunctionCall")
  {
    //  e.g. gasleft() <=> c:@F@gasleft
    // The callee can be wrapped in redundant parens, e.g.
    // `(blockhash)(x)`, which parses the callee as a 1-component
    // TupleExpression around the real Identifier. Unwrap before
    // looking at nodeType.
    const nlohmann::json *callee = &expr["expression"];
    while (callee->is_object() &&
           callee->value("nodeType", "") == "TupleExpression" &&
           callee->contains("components") &&
           (*callee)["components"].is_array() &&
           (*callee)["components"].size() == 1 &&
           !(*callee)["components"][0].is_null())
      callee = &(*callee)["components"][0];

    if ((*callee)["nodeType"].get<std::string>() != "Identifier")
      // this means it's not a builtin function
      return true;

    std::string name = (*callee)["name"].get<std::string>();

    // If the callee already resolves to a user-defined Solidity decl
    // (positive referencedDeclaration), don't shadow it with a C symbol
    // whose base name happens to match — a Solidity function colliding
    // with a stdlib.h export (div/abs/malloc/atoi/sort/…) would otherwise
    // pick up the C decl and its C return type (e.g. div_t struct),
    // tripping "got struct, expected unsignedbv" downstream.
    //
    // Exception: contracts commonly declare stub internal functions
    // named `__ESBMC_assume`/`__ESBMC_assert`/`__VERIFIER_*` so that
    // solc can compile the source standalone. At verification time
    // those user decls MUST be replaced by the real ESBMC intrinsics;
    // the C-symbol fallback is how that happens. Keep the existing
    // hijack for those names only.
    const bool is_intrinsic_alias =
      name == "__ESBMC_assume" || name == "__ESBMC_assert" ||
      name == "__VERIFIER_assume" || name == "__VERIFIER_assert";
    if (
      !is_intrinsic_alias && (*callee).contains("referencedDeclaration") &&
      !(*callee)["referencedDeclaration"].is_null() &&
      (*callee)["referencedDeclaration"].get<int>() > 0)
      return true;

    std::string id = "c:@F@" + name;
    if (context.find_symbol(id) == nullptr)
      return true;
    const symbolt &sym = *context.find_symbol(id);
    new_expr = symbol_expr(sym);
  }
  else if (expr["nodeType"].get<std::string>() == "MemberAccess")
  {
    // e.g. string.concat() <=> c:@string_concat
    std::string bs;
    if (expr.contains("memberName"))
    {
      // assume it's the no-basename type
      // e.g. address(this).balance, type(uint256).max
      std::string name = expr["memberName"];
      if (name == "max" || name == "min")
      {
        exprt dump;
        if (get_expr(expr["expression"], dump))
          return true;
        SolidityGrammar::SolType sol_st = get_sol_type(dump.type());
        // extract integer width: e.g. UINT8 => "UINT" + "8"
        std::string sol_str = SolidityGrammar::sol_type_to_str(sol_st);
        std::string type = (sol_str[0] == 'U') ? "UINT" : "INT";
        std::string width = sol_str.substr(type.size());
        exprt is_signed =
          type == "INT" ? exprt(true_exprt()) : exprt(false_exprt());

        side_effect_expr_function_callt call;
        if (name == "max")
          get_library_function_call_no_args(
            "_max", "c:@F@_max", unsignedbv_typet(256), l, call);
        else
          get_library_function_call_no_args(
            "_min", "c:@F@_min", unsignedbv_typet(256), l, call);
        call.arguments().push_back(constant_exprt(
          integer2binary(string2integer(width), bv_width(uint_type())),
          width,
          uint_type()));
        call.arguments().push_back(is_signed);

        new_expr = call;
        new_expr.location() = l;
        return false;
      }
      else if (
        name == "creationCode" || name == "runtimeCode" || name == "interfaceId")
      {
        // Stable per-name ID. Same `type(C).<name>` returns the same
        // value across reads; distinct contracts/interfaces map to
        // distinct IDs (counter-based, no probability argument).
        // Library helper applies a uint-bijection (`~id`) so the
        // result is a deterministic injection of the source name.
        // Closes ledger #15 — pre-fix the helpers ignored context and
        // returned fresh nondets, breaking real-EVM stability.
        std::string ts =
          expr["expression"]["typeDescriptions"]["typeString"]
            .get<std::string>();
        // Extract name from "type(contract C)" / "type(interface I)" /
        // "type(library L)" patterns (mirrors the `name == "name"`
        // case below).
        std::string cname;
        auto pos = ts.rfind(' ');
        if (pos != std::string::npos && ts.back() == ')')
          cname = ts.substr(pos + 1, ts.size() - pos - 2);
        else
          cname = ts;
        // Per-helper namespace so distinct properties on the same
        // contract (e.g. creationCode vs runtimeCode of contract C)
        // map to distinct IDs.
        std::string key = name + ":" + cname;
        auto it = interface_id_table.find(key);
        uint32_t id;
        if (it == interface_id_table.end())
        {
          id = next_interface_id++;
          interface_id_table[key] = id;
        }
        else
          id = it->second;

        typet ret_t =
          (name == "interfaceId") ? typet(unsignedbv_typet(32)) : uint_type();
        side_effect_expr_function_callt call;
        get_library_function_call_no_args(
          "_" + name, "c:@F@_" + name, ret_t, l, call);
        call.arguments().push_back(
          from_integer(BigInt(id), unsignedbv_typet(32)));
        new_expr = call;
        new_expr.location() = l;
        return false;
      }
      else if (name == "name")
      {
        // type(C).name returns the contract name as a string literal
        std::string ts = expr["expression"]["typeDescriptions"]["typeString"]
                           .get<std::string>();
        // Extract name from "type(contract MyContract)" or "type(interface I)"
        std::string cname;
        auto pos = ts.rfind(' ');
        if (pos != std::string::npos && ts.back() == ')')
          cname = ts.substr(pos + 1, ts.size() - pos - 2);
        else
          cname = ts;

        new_expr = string_constantt(cname);
        new_expr.location() = l;
        return false;
      }
      else if (name == "wrap" || name == "unwrap")
      {
        // do nothhing, return operands
        // The base expression for `<UDVT>.wrap` / `.unwrap` may be:
        //   - Identifier  : `MyInt.wrap(...)`
        //   - TupleExpression wrapping an Identifier : `(MyInt).wrap(...)`
        //   - MemberAccess (qualified via contract): `C.T.wrap(...)`
        // Walk through paren wrappers and resolve via referencedDeclaration
        // when a plain `name` field isn't available.
        const nlohmann::json *base = &expr["expression"];
        while (base->is_object() &&
               base->value("nodeType", "") == "TupleExpression" &&
               base->contains("components") &&
               (*base)["components"].is_array() &&
               (*base)["components"].size() == 1 &&
               !(*base)["components"][0].is_null())
          base = &(*base)["components"][0];

        std::string udv;
        if (base->is_object() && base->contains("name") &&
            (*base)["name"].is_string())
          udv = (*base)["name"].get<std::string>();
        else if (
          base->is_object() && base->value("nodeType", "") == "MemberAccess" &&
          base->contains("memberName") && (*base)["memberName"].is_string())
          udv = (*base)["memberName"].get<std::string>();

        if (udv.empty() || UserDefinedVarMap.count(udv) == 0)
        {
          // Fall back to the typeString. For `(MyInt).wrap`, the base's
          // typeString is `type(MyInt)`. For `C.T.wrap`, typeString is
          // `type(C.T)`. Strip the wrapping `type(...)` and any leading
          // contract qualifier.
          if (base->is_object() && base->contains("typeDescriptions") &&
              (*base)["typeDescriptions"].contains("typeString"))
          {
            std::string ts =
              (*base)["typeDescriptions"]["typeString"].get<std::string>();
            if (ts.compare(0, 5, "type(") == 0 && ts.back() == ')')
              ts = ts.substr(5, ts.size() - 6);
            auto dot = ts.rfind('.');
            if (dot != std::string::npos)
              ts = ts.substr(dot + 1);
            udv = ts;
          }
        }

        if (udv.empty() || UserDefinedVarMap.count(udv) == 0)
        {
          log_error("UDVT wrap/unwrap: cannot resolve base type '{}'", udv);
          return true;
        }
        typet t = UserDefinedVarMap[udv];
        new_expr = typecast_exprt(t); // we will set the op0 later
        new_expr.location() = l;
        return false;
      }
      else if (name == "length")
      {
        exprt base;
        if (get_expr(expr["expression"], base))
          return true;
        typet base_t;
        if (get_type_description(
              expr["expression"]["typeDescriptions"], base_t))
          return true;
        SolidityGrammar::SolType solt = get_sol_type(base_t);
        if (
          solt == SolidityGrammar::SolType::ARRAY ||
          solt == SolidityGrammar::SolType::ARRAY_LITERAL ||
          solt == SolidityGrammar::SolType::DYNARRAY)
        {
          // mapping array: return the auxiliary _length variable
          if (
            solt == SolidityGrammar::SolType::DYNARRAY &&
            base_t.get_bool("#sol_mapping_array"))
          {
            assert(base.is_symbol());
            std::string len_id =
              base.identifier().as_string() + "_mapping_arr_len";
            const symbolt *len_sym = ns.lookup(len_id);
            assert(len_sym);
            new_expr = symbol_expr(*len_sym);
          }
          // mapping(K => V[]) state-var: per-key length aux indexed by k.
          // `base` is `m[k]` = index_exprt(m, folded_k), and m carries the
          // `#sol_mapping_of_dynarr` flag on its type.
          else if (solt == SolidityGrammar::SolType::DYNARRAY &&
                   base.id() == "index" && !base.operands().empty() &&
                   base.op0().id() == "symbol" &&
                   base.op0().type().get_bool("#sol_mapping_of_dynarr"))
          {
            exprt m_sym = base.op0();
            exprt folded_k = base.op1();
            std::string len_id =
              m_sym.identifier().as_string() + "_mapdynarr_len";
            const symbolt *len_sym = ns.lookup(len_id);
            assert(len_sym);
            new_expr = index_exprt(
              symbol_expr(*len_sym), folded_k, unsignedbv_typet(256));
          }
          // dynarray state var: return the auxiliary _dynarray_len variable
          else if (
            solt == SolidityGrammar::SolType::DYNARRAY && base.is_symbol() &&
            base.type().get_bool("#sol_dynarray_state"))
          {
            assert(base.is_symbol());
            std::string len_id =
              base.identifier().as_string() + "_dynarray_len";
            const symbolt *len_sym = ns.lookup(len_id);
            assert(len_sym);
            if (get_dynarr_len_ref(*len_sym, new_expr))
              return true;
          }
          // dynamic array (pointer model)
          else if (solt == SolidityGrammar::SolType::DYNARRAY)
          {
            side_effect_expr_function_callt length_expr;
            get_library_function_call_no_args(
              "_ESBMC_array_length",
              "c:@F@_ESBMC_array_length",
              uint_type(),
              l,
              length_expr);
            length_expr.arguments().push_back(base);
            new_expr = length_expr;
          }
          else
          {
            // static array:  uint[2] arr; arr.length = 2;
            std::string arr_size = base_t.get("#sol_array_size").as_string();
            assert(!arr_size.empty());
            new_expr = constant_exprt(
              integer2binary(string2integer(arr_size), bv_width(uint_type())),
              arr_size,
              uint_type());
          }
        }
        else if (is_byte_type(base_t))
        {
          // The bytes container is normally a struct with a `length` field.
          // But some sources of bytes (e.g. `address.code`, modeled as a
          // uint256 identity token in get_builtin_property_expr) lower to
          // a uint256 primitive that has no such field. In those cases
          // emitting a member_exprt produces an ill-typed access that the
          // SMT layer dereferences and aborts on. Fall back to a nondet
          // uint length — sound for unconstrained external bytes.
          if (base.type().is_unsignedbv() || base.type().is_signedbv())
          {
            get_nondet_expr(uint_type(), new_expr);
          }
          else
          {
            member_exprt len(base, "length", size_type());
            new_expr = len;
          }
        }
        else
        {
          log_error(
            "Unexpected length of {} type",
            SolidityGrammar::sol_type_to_str(solt));
          return true;
        }
        new_expr.location() = l;
        return false;
      }
      else if (name == "push" || name == "pop")
      {
        exprt base;
        if (get_expr(expr["expression"], base))
          return true;

        typet base_t;
        if (get_type_description(
              expr["expression"]["typeDescriptions"], base_t))
          return true;

        SolidityGrammar::SolType solt = get_sol_type(base_t);

        locationt l;
        get_location_from_node(expr, l);

        if (
          solt == SolidityGrammar::SolType::DYNARRAY &&
          base_t.get_bool("#sol_mapping_array"))
        {
          // mapping(K=>V)[]: push increments length, pop decrements.
          assert(base.is_symbol());
          std::string len_id =
            base.identifier().as_string() + "_mapping_arr_len";
          const symbolt *len_sym = ns.lookup(len_id);
          assert(len_sym);
          exprt len_ref = symbol_expr(*len_sym);
          exprt one = constant_exprt(
            integer2binary(1, bv_width(unsignedbv_typet(256))),
            "1",
            unsignedbv_typet(256));
          if (name == "push")
          {
            // length++
            new_expr = side_effect_exprt("assign", len_ref.type());
            new_expr.operands().push_back(len_ref);
            new_expr.operands().push_back(
              gen_binary("+", unsignedbv_typet(256), len_ref, one));
          }
          else
          {
            // length--
            new_expr = side_effect_exprt("assign", len_ref.type());
            new_expr.operands().push_back(len_ref);
            new_expr.operands().push_back(
              gen_binary("-", unsignedbv_typet(256), len_ref, one));
          }
        }
        else if (
          solt == SolidityGrammar::SolType::DYNARRAY && base.is_symbol() &&
          base.type().get_bool("#sol_dynarray_state"))
        {
          // Dynarray state var: write element at len, then increment len.
          // T1.1 Stage S1: `len_ref` is now `<arr>_dynarray_len[this->$address]`
          // (addr-keyed) so two `new C()` instances no longer share length.
          assert(base.is_symbol());
          std::string len_id = base.identifier().as_string() + "_dynarray_len";
          const symbolt *len_sym = ns.lookup(len_id);
          assert(len_sym);
          exprt len_ref;
          if (get_dynarr_len_ref(*len_sym, len_ref))
            return true;
          exprt one = constant_exprt(
            integer2binary(1, bv_width(unsignedbv_typet(256))),
            "1",
            unsignedbv_typet(256));
          if (name == "push")
          {
            // Get the push argument value
            const nlohmann::json &func =
              find_last_parent(src_ast_json["nodes"], expr);
            assert(!func.empty());

            typet elem_type = base_t.subtype();

            // items[len] = value — T1.1 Stage S2: addr-keyed via
            // get_dynarr_elem_idx so two instances no longer alias.
            exprt fold_idx;
            if (get_dynarr_elem_idx(len_ref, fold_idx))
              return true;
            exprt idx_expr = index_exprt(base, fold_idx, elem_type);
            exprt assign_elem = side_effect_exprt("assign", elem_type);

            if (func["arguments"].size() == 0)
            {
              // push() with no args: zero value
              exprt zero_val = gen_zero(elem_type);
              assign_elem.copy_to_operands(idx_expr, zero_val);
            }
            else
            {
              exprt val;
              if (get_expr(func["arguments"][0], expr["argumentTypes"][0], val))
                return true;
              solidity_gen_typecast(ns, val, elem_type);
              assign_elem.copy_to_operands(idx_expr, val);
            }
            convert_expression_to_code(assign_elem);
            move_to_front_block(assign_elem);

            // len = len + 1
            new_expr = side_effect_exprt("assign", len_ref.type());
            new_expr.operands().push_back(len_ref);
            new_expr.operands().push_back(
              gen_binary("+", unsignedbv_typet(256), len_ref, one));
          }
          else
          {
            // pop: len = len - 1
            new_expr = side_effect_exprt("assign", len_ref.type());
            new_expr.operands().push_back(len_ref);
            new_expr.operands().push_back(
              gen_binary("-", unsignedbv_typet(256), len_ref, one));
          }
        }
        else if (
          solt == SolidityGrammar::SolType::ARRAY ||
          solt == SolidityGrammar::SolType::ARRAY_LITERAL ||
          solt == SolidityGrammar::SolType::DYNARRAY)
        {
          // Original array push/pop logic (pointer-based model)
          assert(base_t.has_subtype());
          exprt size_of;
          get_size_of_expr(base_t.subtype(), size_of);

          const nlohmann::json &func =
            find_last_parent(src_ast_json["nodes"], expr);
          assert(!func.empty());
          exprt args;
          if (func["arguments"].size() == 0)
          {
            // Generate a default value for the element type
            exprt default_value = gen_zero(base_t.subtype());
            std::string aux_name = "_tmpzero#" + std::to_string(aux_counter++);
            std::string aux_id;
            std::string cname;
            get_current_contract_name(expr, cname);
            assert(!cname.empty());
            if (current_functionDecl)
              aux_id = "sol:@C@" + cname + "@F@" + current_functionName + "@" +
                       aux_name + "#" + std::to_string(aux_counter++);
            else
              aux_id = "sol:@C@" + cname + "@" + aux_name + "#" +
                       std::to_string(aux_counter++);

            symbolt aux_sym;
            get_default_symbol(
              aux_sym,
              get_modulename_from_path(absolute_path),
              base_t.subtype(),
              aux_name,
              aux_id,
              l);
            aux_sym.lvalue = true;
            aux_sym.file_local = true;

            auto &inserted = *move_symbol_to_context(aux_sym);
            inserted.value = default_value;

            code_declt decl(symbol_expr(inserted));
            decl.operands().push_back(default_value);
            move_to_front_block(decl);

            args = address_of_exprt(symbol_expr(inserted));
          }
          else
          {
            if (get_expr(func["arguments"][0], expr["argumentTypes"][0], args))
              return true;

            std::string aux_name = "_idx#" + std::to_string(aux_counter++);
            std::string aux_id;
            std::string cname;
            get_current_contract_name(expr, cname);
            assert(!cname.empty());
            if (current_functionDecl)
              aux_id = "sol:@C@" + cname + "@F@" + current_functionName + "@" +
                       aux_name + "#" + std::to_string(aux_counter++);
            else
              aux_id = "sol:@C@" + cname + "@" + aux_name + "#" +
                       std::to_string(aux_counter++);
            symbolt aux_idx;
            get_default_symbol(
              aux_idx,
              get_modulename_from_path(absolute_path),
              args.type(),
              aux_name,
              aux_id,
              l);
            auto &added_aux = *move_symbol_to_context(aux_idx);
            code_declt decl(symbol_expr(added_aux));
            added_aux.value = args;
            decl.operands().push_back(args);
            move_to_front_block(decl);
            args = address_of_exprt(symbol_expr(added_aux));
          }

          /* Route uint256-element pushes on a mapping-backing slot
           * through the typed helper `_ESBMC_array_push_uint256`. The
           * generic `_ESBMC_array_push` uses `__builtin_memcpy` whose
           * byte-loop (`__memcpy_impl`) is silently truncated under
           * `--unwind N --no-unwinding-assertions`, killing the
           * post-push path before writebacks reach the mapping slot.
           * The typed helper assigns the last element without a loop.
           *
           * Detection heuristic: `base` is an `index_exprt` into an
           * infinite array that is NOT flagged as a state-var dynarray.
           * State-var dynarrays (`#sol_dynarray_state`) use the earlier
           * branch that hand-writes the element into the next slot, so
           * they don't reach this point; nested dynarrays like
           * `a2[0].push(x)` DO reach this point but have
           * `#sol_dynarray_state` on their outer array, so they stay on
           * the legacy helper. Mapping backings (generated as
           * `array_typet(V, infinity)` with no `#sol_dynarray_state`
           * flag) switch over. */
          /* Restrict to top-level mapping slot access — `m[k]` whose base
           * expression is the mapping symbol directly. `a2[0].push(x)`
           * on a state-var 2D dynarray (`uint256[][]`) also has an
           * `index_exprt` base but its op0 is the flagged state-var
           * symbol, so the `#sol_dynarray_state` check excludes it.
           * Nested accesses like `deep[0][0].push(x)` (from
           * `nested_array_deep_1`) have op0 = another index_exprt — not
           * a symbol — and must NOT take this path: they ride on the
           * legacy `_ESBMC_array_push` which the nested state-var
           * dynarray model depends on. */
          bool is_mapping_backing_slot =
            name == "push" && base.id() == "index" &&
            !base.operands().empty() &&
            base.op0().id() == "symbol" &&
            base.op0().type().is_array() &&
            !base.op0().type().get_bool("#sol_dynarray_state") &&
            base_t.id() == "pointer" &&
            base_t.subtype().id() == "unsignedbv" &&
            base_t.subtype().get("width").as_string() == "256";

          side_effect_expr_function_callt mem;
          if (is_mapping_backing_slot)
          {
            /* typed helper: (array, element-by-value) returning void*. */
            get_library_function_call_no_args(
              "_ESBMC_array_push_uint256",
              "c:@F@_ESBMC_array_push_uint256",
              pointer_typet(empty_typet()),
              l,
              mem);
            /* args above was set to `address_of(aux)`; the typed helper
             * takes the element by value, so pass the aux directly. */
            exprt elem_by_value = args;
            if (elem_by_value.id() == "address_of" &&
                !elem_by_value.operands().empty())
              elem_by_value = elem_by_value.op0();
            solidity_gen_typecast(
              ns, elem_by_value, unsignedbv_typet(256));
            mem.arguments().push_back(base);
            mem.arguments().push_back(elem_by_value);
          }
          else
          {
            get_library_function_call_no_args(
              "_ESBMC_array_" + name,
              "c:@F@_ESBMC_array_" + name,
              empty_typet(),
              l,
              mem);

            if (name == "push")
            {
              mem.arguments().push_back(base);
              mem.arguments().push_back(args);
              mem.arguments().push_back(size_of);
            }
            else
            {
              mem.arguments().push_back(base);
              mem.arguments().push_back(size_of);
            }
          }

          new_expr = mem;
        }
        else if (is_bytes_type(base_t))
        {
          // Support for bytes.push / bytes.pop
          side_effect_expr_function_callt mem;
          std::string fname =
            (name == "push") ? "bytes_dynamic_push" : "bytes_dynamic_pop";
          get_library_function_call_no_args(
            fname, "c:@F@" + fname, empty_typet(), l, mem);

          exprt pool_member;
          if (get_dynamic_pool(expr, pool_member))
            return true;
          mem.arguments().push_back(address_of_exprt(base));
          if (name == "push")
          {
            exprt value_expr;
            const nlohmann::json &func =
              find_last_parent(src_ast_json["nodes"], expr);
            assert(!func.empty());

            if (func["arguments"].size() == 0)
              // x.push() == x.push(0x00)
              value_expr = gen_zero(uint_type());
            else if (get_expr(
                       func["arguments"][0],
                       expr["argumentTypes"][0],
                       value_expr))
              return true;

            // push value must be byte-sized
            if (value_expr.type() != unsigned_char_type())
              solidity_gen_typecast(ns, value_expr, unsigned_char_type());
            mem.arguments().push_back(value_expr);
          }
          mem.arguments().push_back(pool_member);

          new_expr = mem;
        }
        else
        {
          log_error(
            "Unexpected .{}() on non-array/bytes type: {}",
            name,
            SolidityGrammar::sol_type_to_str(solt));
          return true;
        }
        new_expr.location() = l;
        return false;
      }
      else if (name == "concat")
      {
        // string.concat(...) or bytes.concat(...)
        // Determine base type name from the ElementaryTypeNameExpression
        std::string base_name;
        if (
          expr["expression"].contains("typeName") &&
          expr["expression"]["typeName"].contains("name"))
          base_name = expr["expression"]["typeName"]["name"].get<std::string>();
        else if (expr["expression"].contains("name"))
          base_name = expr["expression"]["name"].get<std::string>();
        else
          return true;

        // Get arguments from parent FunctionCall node
        const nlohmann::json &func_call =
          find_last_parent(src_ast_json["nodes"], expr);
        assert(!func_call.empty() && func_call.contains("arguments"));

        const auto &args_json = func_call["arguments"];
        size_t nargs = args_json.size();
        if (nargs < 2)
          return true;

        // Convert all arguments
        std::vector<exprt> args;
        for (const auto &arg : args_json)
        {
          exprt a;
          if (get_expr(arg, arg["typeDescriptions"], a))
            return true;
          args.push_back(a);
        }

        if (base_name == "string")
        {
          // string.concat: fold N-ary into nested binary string_concat calls
          const symbolt *sym = context.find_symbol("c:@F@string_concat");
          if (!sym)
            return true;

          side_effect_expr_function_callt first;
          get_library_function_call_no_args(
            "string_concat", "c:@F@string_concat", sym->type, l, first);
          first.arguments().push_back(args[0]);
          first.arguments().push_back(args[1]);

          exprt result = first;
          for (size_t i = 2; i < nargs; i++)
          {
            side_effect_expr_function_callt next;
            get_library_function_call_no_args(
              "string_concat", "c:@F@string_concat", sym->type, l, next);
            next.arguments().push_back(result);
            next.arguments().push_back(args[i]);
            result = next;
          }
          new_expr = result;
        }
        else if (base_name == "bytes")
        {
          // bytes.concat: fold into nested binary bytes_dynamic_concat calls
          exprt pool_member;
          if (get_dynamic_pool(expr, pool_member))
            return true;

          const symbolt *sym = context.find_symbol("c:@F@bytes_dynamic_concat");
          if (!sym)
            return true;

          side_effect_expr_function_callt first;
          get_library_function_call_no_args(
            "bytes_dynamic_concat",
            "c:@F@bytes_dynamic_concat",
            sym->type,
            l,
            first);
          first.arguments().push_back(args[0]);
          first.arguments().push_back(args[1]);
          first.arguments().push_back(pool_member);

          exprt result = first;
          for (size_t i = 2; i < nargs; i++)
          {
            side_effect_expr_function_callt next;
            get_library_function_call_no_args(
              "bytes_dynamic_concat",
              "c:@F@bytes_dynamic_concat",
              sym->type,
              l,
              next);
            next.arguments().push_back(result);
            next.arguments().push_back(args[i]);
            next.arguments().push_back(pool_member);
            result = next;
          }
          new_expr = result;
        }
        else
          return true;

        new_expr.location() = l;
        return false;
      }
    }
    if (expr["expression"].contains("name"))
      bs = expr["expression"]["name"].get<std::string>();
    else if (
      expr["expression"].contains("typeName") &&
      expr["expression"]["typeName"].contains("name"))
      bs = expr["expression"]["typeName"]["name"].get<std::string>();
    else
      // cannot get bs name;
      return true;

    std::string mem = expr["memberName"].get<std::string>();
    std::string id_var = "c:@" + bs + "_" + mem;
    std::string id_func = "c:@F@" + bs + "_" + mem;
    if (context.find_symbol(id_var) != nullptr)
    {
      symbolt &sym = *context.find_symbol(id_var);

      if (sym.value.is_empty() || sym.value.is_zero())
      {
        // update: set the value to rand (default 0）
        // since all the current support built-in vars are uint type.
        // we just set the value to c:@F@nondet_uint
        symbolt &r = *context.find_symbol("c:@F@nondet_uint");
        sym.value = r.value;
      }
      new_expr = symbol_expr(sym);
    }

    else if (context.find_symbol(id_func) != nullptr)
      new_expr = symbol_expr(*context.find_symbol(id_func));
    else
      return true;
  }
  else
    return true;

  new_expr.location() = l;
  return false;
}
