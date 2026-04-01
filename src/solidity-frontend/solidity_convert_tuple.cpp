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

bool solidity_convertert::get_tuple_definition(const nlohmann::json &ast_node)
{
  log_debug("solidity", "\t@@@ Parsing tuple...");

  std::string current_contractName;
  get_current_contract_name(ast_node, current_contractName);
  if (current_contractName.empty())
  {
    log_error(
      "Cannot get the contract name. Tuple should always within a contract.");
    return true;
  }

  struct_typet t = struct_typet();

  // get name/id:
  std::string name, id;
  get_tuple_name(ast_node, name, id);

  // get type:
  t.tag("struct " + name);

  // get location
  locationt location_begin;
  get_location_from_node(ast_node, location_begin);

  // get debug module name
  std::string debug_modulename =
    get_modulename_from_path(location_begin.file().as_string());

  // populate struct type symbol
  symbolt symbol;
  get_default_symbol(symbol, debug_modulename, t, name, id, location_begin);
  symbol.static_lifetime = true;
  symbol.file_local = true;
  symbolt &added_symbol = *move_symbol_to_context(symbol);

  auto &args = ast_node.contains("components")
                 ? ast_node["components"]
                 : ast_node["returnParameters"]["parameters"];

  // populate params
  //TODO: flatten the nested tuple (e.g. ((x,y),z) = (func(),1); )
  size_t counter = 0;
  for (const auto &arg : args.items())
  {
    if (arg.value().is_null())
    {
      ++counter;
      continue;
    }

    struct_typet::componentt comp;

    // manually create a member_name
    // follow the naming rule defined in get_local_var_decl_name
    assert(!current_contractName.empty());
    const std::string mem_name = "mem" + std::to_string(counter);
    const std::string mem_id = "sol:@C@" + current_contractName + "@" + name +
                               "@" + mem_name + "#" +
                               i2string(ast_node["id"].get<std::int16_t>());

    // get type
    typet mem_type;
    if (get_type_description(arg.value()["typeDescriptions"], mem_type))
      return true;

    // construct comp
    comp.type() = mem_type;
    comp.type().set("#member_name", t.tag());
    comp.identifier(mem_id);
    comp.cmt_lvalue(true);
    comp.name(mem_name);
    comp.pretty_name(mem_name);
    comp.set_access("internal");

    // update struct type component
    t.components().push_back(comp);

    // update cnt
    ++counter;
  }

  t.location() = location_begin;
  added_symbol.type = t;

  return false;
}

bool solidity_convertert::get_tuple_instance(
  const nlohmann::json &ast_node,
  exprt &new_expr)
{
  std::string name, id;
  get_tuple_name(ast_node, name, id);

  if (context.find_symbol(id) == nullptr)
    return true;

  // get type
  typet t = context.find_symbol(id)->type;
  set_sol_type(t, SolidityGrammar::SolType::TUPLE_INSTANCE);
  assert(t.id() == typet::id_struct);

  // get instance name,id
  if (get_tuple_instance_name(ast_node, name, id))
    return true;

  // get location
  locationt location_begin;
  get_location_from_node(ast_node, location_begin);

  // get debug module name
  std::string debug_modulename =
    get_modulename_from_path(location_begin.file().as_string());

  // populate struct type symbol
  symbolt symbol;
  get_default_symbol(symbol, debug_modulename, t, name, id, location_begin);
  symbol.static_lifetime = true;
  symbol.file_local = true;

  symbol.value = gen_zero(get_complete_type(t, ns), true);
  symbol.value.zero_initializer(true);
  symbolt &added_symbol = *move_symbol_to_context(symbol);
  new_expr = symbol_expr(added_symbol);
  new_expr.identifier(id);

  if (!ast_node.contains("components"))
  {
    // assume it's function return parameter list
    // therefore no initial value
    return false;
  }

  // do assignment
  auto &args = ast_node["components"];

  size_t i = 0;
  size_t j = 0;
  unsigned is = to_struct_type(t).components().size();
  unsigned as = args.size();
  assert(is <= as);

  exprt comp;
  exprt member_access;
  while (i < is && j < as)
  {
    if (args.at(j).is_null())
    {
      ++j;
      continue;
    }

    comp = to_struct_type(t).components().at(i);
    if (get_tuple_member_call(id, comp, member_access))
      return true;

    exprt init;
    const nlohmann::json &litera_type = args.at(j)["typeDescriptions"];

    if (get_expr(args.at(j), litera_type, init))
      return true;

    get_tuple_assignment(ast_node, member_access, init);

    // update
    ++i;
    ++j;
  }

  return false;
}

void solidity_convertert::get_tuple_name(
  const nlohmann::json &ast_node,
  std::string &name,
  std::string &id)
{
  name = "tuple" + std::to_string(ast_node["id"].get<int>());
  id = prefix + "struct " + name;
}

bool solidity_convertert::get_tuple_instance_name(
  const nlohmann::json &ast_node,
  std::string &name,
  std::string &id)
{
  std::string c_name;
  get_current_contract_name(ast_node, c_name);
  if (c_name.empty())
    return true;

  name = "tuple_instance$" + std::to_string(ast_node["id"].get<int>());
  id = "sol:@C@" + c_name + "@" + name;
  return false;
}

/*
  obtain the corresponding tuple struct instance from the symbol table
  based on the function definition json
*/
bool solidity_convertert::get_tuple_function_ref(
  const nlohmann::json &ast_node,
  exprt &new_expr)
{
  assert(ast_node.contains("nodeType") && ast_node["nodeType"] == "Identifier");

  std::string c_name;
  get_current_contract_name(ast_node, c_name);
  if (c_name.empty())
    return true;

  std::string name =
    "tuple_instance$" +
    std::to_string(ast_node["referencedDeclaration"].get<int>());
  std::string id = "sol:@C@" + c_name + "@" + name;

  if (context.find_symbol(id) == nullptr)
    return true;

  new_expr = symbol_expr(*context.find_symbol(id));
  return false;
}

// Knowing that there is a component x in the struct_tuple_instance A, we construct A.x
bool solidity_convertert::get_tuple_member_call(
  const irep_idt instance_id,
  const exprt &comp,
  exprt &new_expr)
{
  // tuple_instance
  assert(!instance_id.empty());
  exprt base;
  if (context.find_symbol(instance_id) == nullptr)
    return true;

  base = symbol_expr(*context.find_symbol(instance_id));
  new_expr = member_exprt(base, comp.name(), comp.type());
  return false;
}

void solidity_convertert::get_tuple_function_call(const exprt &op)
{
  assert(op.id() == "sideeffect");
  exprt func_call = op;
  convert_expression_to_code(func_call);
  if (current_functionDecl)
    move_to_back_block(func_call);
  else
    move_to_initializer(func_call);
}

void solidity_convertert::get_llc_ret_tuple(symbolt &s)
{
  log_debug("solidity", "\tconvert return value to tuple");
  std::string _id = lib_prefix + "sol_llc_ret";
  if (context.find_symbol(_id) == nullptr)
  {
    log_error("cannot find library symbol {}", _id);
    abort();
  }
  const symbolt &struct_sym = *context.find_symbol(_id);

  typet sym_t = struct_sym.type;
  set_sol_type(sym_t, SolidityGrammar::SolType::TUPLE_INSTANCE);

  std::string name, id;
  name = "tuple_instance$" + std::to_string(aux_counter);
  id = "sol:@" + name;
  locationt l;
  symbolt symbol;
  get_default_symbol(
    symbol, get_modulename_from_path(absolute_path), sym_t, name, id, l);
  symbol.static_lifetime = true;
  symbol.file_local = true;
  auto &added_sym = *move_symbol_to_context(symbol);

  // value
  typet t = struct_sym.type;
  exprt inits = gen_zero(t);
  // Cast nondet_bool to match the struct field type (C frontend compiles
  // _Bool/bool as unsigned int in struct layout due to padding)
  exprt bool_val = nondet_bool_expr;
  if (inits.op0().type() != nondet_bool_expr.type())
  {
    typecast_exprt cast(nondet_bool_expr, inits.op0().type());
    bool_val = cast;
  }
  inits.op0() = bool_val;
  inits.op1() = nondet_uint_expr;
  added_sym.value = inits;
  s = added_sym;
}

void solidity_convertert::get_string_assignment(
  const exprt &lhs,
  const exprt &rhs,
  exprt &new_expr)
{
  if (
    rhs.id() == "string-constant" ||
    (lhs.id() == "member" && lhs.component_name() == "_ESBMC_bind_cname"))
  {
    // todo: for immutable var, we can just use assign
    // char * this->bind_name = (const char *)_ESBMC_get_nondet_cont_name"
    // since we do not change the value of bind_name so we should be fine
    side_effect_exprt _assign("assign", lhs.type());
    exprt new_rhs = rhs;
    // pass a dump json as we will not reach bytes part anyway
    convert_type_expr(ns, new_rhs, lhs, empty_json);
    _assign.copy_to_operands(lhs, new_rhs);
    new_expr = _assign;
  }
  else
  {
    //? always assign it to null first?
    exprt null_str = gen_zero(pointer_typet(signed_char_type()));
    side_effect_exprt _assign("assign", lhs.type());
    _assign.location() = lhs.location();
    _assign.copy_to_operands(lhs, null_str);
    move_to_front_block(_assign);

    side_effect_expr_function_callt call;
    get_str_assign_function_call(lhs.location(), call);
    call.arguments().push_back(address_of_exprt(lhs));
    call.arguments().push_back(rhs);
    new_expr = call;
  }
}

/*
  lhs: code_blockt — each operand is a target expr or nil (omitted slot)
  rhs: tuple_return / tuple_instance — a struct symbol with mem0, mem1, ... components

  Uses explicit position-based matching: LHS position i maps to RHS component "mem{i}".
  This is robust regardless of which positions are omitted on either side.
*/
bool solidity_convertert::construct_tuple_assigments(
  const nlohmann::json &expr,
  const exprt &lhs,
  const exprt &rhs)
{
  log_debug("solidity", "Handling tuple assignment.");

  typet rt = rhs.type();
  SolidityGrammar::SolType rt_sol = get_sol_type(rt);

  assert(lhs.type().is_code() && to_code(lhs).statement() == "block");
  exprt new_rhs = rhs;
  if (rt_sol == SolidityGrammar::SolType::TUPLE_RETURNS)
  {
    // (x,y) = func();
    // => func() populates tuple instance; then extract members
    if (get_tuple_function_ref(expr["rightHandSide"]["expression"], new_rhs))
      return true;

    get_tuple_function_call(rhs);
  }

  if (!new_rhs.type().is_struct())
  {
    log_error("expecting struct type for tuple RHS, got {}", new_rhs);
    return true;
  }

  // Build name → component map for the RHS struct
  const struct_typet &rhs_struct = to_struct_type(new_rhs.type());
  std::map<std::string, exprt> rhs_components;
  for (const auto &comp : rhs_struct.components())
    rhs_components[comp.get_name().as_string()] = comp;

  // Match LHS targets to RHS components by position
  std::set<exprt> assigned_symbol;
  for (size_t i = 0; i < lhs.operands().size(); i++)
  {
    exprt lop = lhs.operands().at(i);
    if (lop.is_nil() || assigned_symbol.count(lop))
      continue;
    assigned_symbol.insert(lop);

    // Look up RHS component by positional name "mem{i}"
    std::string mem_name = "mem" + std::to_string(i);
    auto it = rhs_components.find(mem_name);
    if (it == rhs_components.end())
    {
      log_error(
        "tuple assignment: cannot find RHS component '{}' for position {}",
        mem_name,
        i);
      return true;
    }

    exprt rop;
    if (get_tuple_member_call(new_rhs.identifier(), it->second, rop))
      return true;

    get_tuple_assignment(expr, lop, rop);
  }
  return false;
}

void solidity_convertert::get_tuple_assignment(
  const nlohmann::json &expr,
  const exprt &lop,
  exprt rop)
{
  exprt assign_expr;
  if (get_sol_type(lop.type()) == SolidityGrammar::SolType::STRING)
    get_string_assignment(lop, rop, assign_expr);
  else
  {
    assign_expr = side_effect_exprt("assign", lop.type());
    convert_type_expr(ns, rop, lop, expr);
    assign_expr.copy_to_operands(lop, rop);
  }
  convert_expression_to_code(assign_expr);
  if (current_functionDecl)
    move_to_back_block(assign_expr);
  else
    move_to_initializer(assign_expr);
}
