/// \file solidity_convert.h
/// \brief Core AST-to-irep2 converter for the Solidity frontend.
///
/// Declares solidity_convertert, which walks the solc JSON AST and translates
/// Solidity contracts, functions, statements, expressions, and types into
/// ESBMC's irep2 intermediate representation. This is the central class of
/// the Solidity frontend; its methods are split across multiple .cpp files
/// organized by conversion category (declarations, expressions, statements,
/// types, references, builtins, etc.).

#ifndef SOLIDITY_FRONTEND_SOLIDITY_CONVERT_H_
#define SOLIDITY_FRONTEND_SOLIDITY_CONVERT_H_

#include <memory>
#include <stack>
#include <vector>
#include <map>
#include <queue>
#include <util/context.h>
#include <util/namespace.h>
#include <util/std_types.h>
#include <util/std_code.h>
#include <util/string_constant.h>
#include <nlohmann/json.hpp>
#include <solidity-frontend/solidity_grammar.h>
#include <solidity-frontend/pattern_check.h>
#include <util/symbolic_types.h>
#include <util/expr_util.h>

class solidity_convertert
{
public:
  solidity_convertert(
    contextt &_context,
    nlohmann::json &_ast_json,
    const std::string &_sol_cnts,
    const std::string &_sol_func,
    const std::string &_contract_path,
    const std::string &_focus_func = std::string());
  virtual ~solidity_convertert() = default;

  bool convert();
  static bool is_low_level_call(const std::string &name);
  static bool is_low_level_property(const std::string &name);

  // find reference
  static const nlohmann::json &
  find_last_parent(const nlohmann::json &json, const nlohmann::json &target);
  static const nlohmann::json &find_parent_contract(
    const nlohmann::json &json,
    const nlohmann::json &target);

  // Pure DFS: find node with matching "id" field in any JSON subtree.
  static const nlohmann::json &
  find_node_by_id(const nlohmann::json &subtree, int ref_id);

  // Scoped declaration lookup: searches current_baseContractName + libraries
  // + global scope. Handles virtual/override via overrideMap.
  // After inheritance merging, node IDs are not unique across contracts,
  // so this function restricts the search to the correct scope.
  const nlohmann::json &find_decl_ref(int ref_id);

  static const nlohmann::json &find_constructor_ref(int ref_decl_id);
  static const nlohmann::json &
  find_constructor_ref(const std::string &contract_name);

  // Set/get SolType enum on a typet via the #sol_type irep attribute.
  static void set_sol_type(typet &t, SolidityGrammar::SolType st)
  {
    t.set("#sol_type", SolidityGrammar::sol_type_to_str(st));
  }
  static SolidityGrammar::SolType get_sol_type(const typet &t)
  {
    return SolidityGrammar::str_to_sol_type(t.get("#sol_type").as_string());
  }

  // json nodes that always empty
  // used as the return value for find_constructor_ref when
  // dealing with the implicit constructor call
  // this is to avoid reference to stack memory associated with local variable returned
  static const nlohmann::json empty_json;
  //! Be careful of using 'current_contractName'. This might lead to trouble in inheritance.
  //! If you are not sure, use 'get_current_contract_name' instead.
  static std::string current_baseContractName;

  // json for Solidity AST. Use object for contract
  static nlohmann::json src_ast_json;
  // User-Defined Variable Mapping
  // e.g. type UFixed256x18 is uint256;
  static std::unordered_map<std::string, typet> UserDefinedVarMap;

protected:
  typedef struct func_sig
  {
    std::string name;
    std::string id;
    std::string visibility;
    code_typet type;
    bool is_payable;
    bool is_inherit;
    bool is_library;

    func_sig(
      const std::string &name,
      const std::string &id,
      const std::string &visibility,
      const code_typet &type,
      bool is_payable,
      bool is_inherit,
      bool is_library)
      : name(name),
        id(id),
        visibility(visibility),
        type(type),
        is_payable(is_payable),
        is_inherit(is_inherit),
        is_library(is_library)
    {
    }

    bool operator==(const func_sig &other) const
    {
      return id == other.id;
    }
  } func_sig;

  void merge_multi_files();
  void topological_sort(
    std::unordered_map<std::string, std::unordered_set<std::string>> &graph,
    std::unordered_map<std::string, nlohmann::json> &path_to_json,
    nlohmann::json &sorted_files);
  bool contract_precheck();
  bool check_sol_ver();
  bool populate_auxiliary_vars();
  // AST preprocessing: specialize internal function-pointer parameters at
  // static-callback call sites by cloning the callee with the fn-ptr
  // parameters erased and their indirect calls rewritten to direct calls.
  // Runs before symbol registration. See solidity_monomorphize.cpp.
  bool monomorphize_fn_ptr_params();
  bool
  populate_function_signature(nlohmann::json &json, const std::string &cname);
  bool populate_low_level_functions(
    const std::string &cname,
    bool is_library = false);
  // Lazily emit `void _ESBMC_enclosing_debit(uint256 val)`: a helper
  // that debits `val` from the currently-executing contract's
  // $balance.  Dispatch is by pointer identity against each
  // `_ESBMC_Object_<C>` instance, using the `_ESBMC_enclosing_contract_this`
  // ambient set at every contract-method entry.  Called from library
  // $transfer/$call#1/$send bodies on the caller-side debit path
  // (libraries don't own their own $balance slot).
  bool build_enclosing_debit_helper();
  bool convert_ast_nodes(
    const nlohmann::json &contract_def,
    const std::string &cname);
  void get_cname_expr(const std::string &cname, exprt &new_expr);

  // conversion functions
  // get decl in rule contract-body-element
  bool get_contract_definition(const std::string &c_name);
  bool get_non_function_decl(const nlohmann::json &ast_node, exprt &new_expr);
  bool get_function_decl(const nlohmann::json &ast_node);
  bool get_var_decl(
    const nlohmann::json &ast_node,
    const nlohmann::json &initialValue,
    exprt &new_expr);
  bool get_var_decl(const nlohmann::json &ast_node, exprt &new_expr);
  bool get_function_definition(const nlohmann::json &ast_node);
  bool add_reentry_check(
    const std::string &c_name,
    const locationt &loc,
    exprt &body_exprt);
  bool get_func_modifier(
    const nlohmann::json &ast_node,
    const std::string &c_name,
    const std::string &f_name,
    const std::string &f_id,
    const bool add_reentry,
    exprt &body_exprt);
  bool has_modifier_invocation(const nlohmann::json &ast_node);
  bool insert_modifier_json(
    const nlohmann::json &ast_node,
    const std::string &cname,
    const std::string &fname,
    nlohmann::json *&modifier_def);
  bool delete_modifier_json(
    const std::string &cname,
    const std::string &fname,
    nlohmann::json *&modifier_def);
  void get_modifier_function_name(
    const std::string &cname,
    const std::string &mod_name,
    const std::string &func_name,
    std::string &name,
    std::string &id);
  bool get_function_params(
    const nlohmann::json &pd,
    const std::string &cname,
    exprt &param);
  void get_function_this_pointer_param(
    const std::string &contract_name,
    const std::string &ctor_id,
    const std::string &debug_modulename,
    const locationt &location_begin,
    code_typet &type);
  bool get_unbound_funccall(
    const std::string contractName,
    code_function_callt &call);
  void get_static_contract_instance_name(
    const std::string c_name,
    std::string &name,
    std::string &id);
  void add_static_contract_instance(const std::string c_name);
  void
  get_static_contract_instance_ref(const std::string &c_name, exprt &new_expr);
  void get_inherit_static_contract_instance_name(
    const std::string bs_c_name,
    const std::string c_name,
    std::string &name,
    std::string &id);
  void get_inherit_static_contract_instance(
    const std::string bs_c_name,
    const std::string c_name,
    const nlohmann::json &args_list,
    symbolt &sym);
  void get_inherit_ctor_definition(const std::string c_name, exprt &new_expr);
  void get_inherit_ctor_definition_name(
    const std::string c_name,
    std::string &name,
    std::string &id);
  void get_contract_mutex_name(
    const std::string c_name,
    std::string &name,
    std::string &id);
  void get_contract_mutex_expr(
    const std::string c_name,
    const exprt &this_expr,
    exprt &expr);
  bool get_high_level_call_wrapper(
    const std::string c_name,
    const exprt &this_expr,
    exprt &front_block,
    exprt &back_block,
    bool is_library = false);
  bool is_sol_builin_symbol(const std::string &cname, const std::string &name);
  nlohmann::json reorder_arguments(
    const nlohmann::json &expr,
    const nlohmann::json &src_ast_json,
    const nlohmann::json &callee_expr_json);

  // handle the non-contract definition, including struct/enum/error/event/abstract/...
  bool get_noncontract_defition(nlohmann::json &ast_node);
  bool
  get_noncontract_decl_ref(const nlohmann::json &ast_node, exprt &new_expr);
  bool get_struct_class(const nlohmann::json &ast_node);
  void add_enum_member_val(nlohmann::json &ast_node);
  bool get_error_definition(const nlohmann::json &ast_node);
  void add_empty_body_node(nlohmann::json &ast_node);

  // handle inheritance
  void merge_inheritance_ast(
    const std::string &c_name,
    nlohmann::json &c_node,
    std::set<std::string> &merged_list);
  void add_inherit_label(nlohmann::json &node, const std::string &cname);

  // handle constructor
  bool get_constructor(
    const nlohmann::json &ast_node,
    const std::string &contract_name);
  bool add_implicit_constructor(const std::string &contract_name);
  bool get_implicit_ctor_ref(
    const std::string &contract_name,
    const bool is_object,
    exprt &new_expr);
  bool get_instantiation_ctor_call(
    const std::string &contract_name,
    exprt &new_expr);
  void move_to_initializer(const exprt &expr);
  bool move_initializer_to_ctor(
    const nlohmann::json *based_contracts,
    const nlohmann::json &current_contract,
    const std::string contract_name);
  bool move_initializer_to_ctor(
    const nlohmann::json *based_contracts,
    const nlohmann::json &current_contract,
    const std::string contract_name,
    bool is_aux_ctor);
  bool move_initializer_to_main(codet &func_body);
  bool move_inheritance_to_ctor(
    const nlohmann::json *based_contracts,
    const std::string contract_name,
    std::string ctor_id,
    symbolt &sym);
  void move_to_front_block(const exprt &expr);
  void move_to_back_block(const exprt &expr);

  // Symbol id of a library function's formal parameter (pure — no reliance on
  // current_functionName). Shared between the library function body builder
  // and the call-site copy-back logic so the ID format stays in one place.
  static std::string get_library_param_id(
    const std::string &lib_cname,
    const std::string &func_name,
    const std::string &param_name,
    int param_ast_id);

  // handle contract variables and functions
  bool
  get_struct_class_fields(const nlohmann::json &ast_node, struct_typet &type);
  bool
  get_struct_class_method(const nlohmann::json &ast_node, struct_typet &type);

  bool get_access_from_decl(
    const nlohmann::json &ast_node,
    struct_typet::componentt &comp);
  bool get_block(
    const nlohmann::json &expr,
    exprt &
      new_expr); // For Solidity's mutually inclusive: rule block and rule statement
  bool get_statement(const nlohmann::json &block, exprt &new_expr);
  bool get_expr(const nlohmann::json &expr, exprt &new_expr);
  bool get_expr(
    const nlohmann::json &expr,
    const nlohmann::json &expr_common_type,
    exprt &new_expr);
  bool get_init_expr(
    const nlohmann::json &init_value,
    const nlohmann::json &literal_type,
    const typet &dest_type,
    exprt &new_expr);
  // Expression handlers (extracted from get_expr switch)
  bool get_decl_ref_expr(const nlohmann::json &expr, exprt &new_expr);
  bool get_literal_expr(
    const nlohmann::json &expr,
    const nlohmann::json &literal_type,
    exprt &new_expr);
  bool get_tuple_expr(
    const nlohmann::json &expr,
    const nlohmann::json &literal_type,
    exprt &new_expr);
  bool get_call_expr(
    const nlohmann::json &expr,
    const nlohmann::json &literal_type,
    exprt &new_expr);
  bool get_contract_member_call_expr(
    const nlohmann::json &expr,
    const nlohmann::json &literal_type,
    exprt &new_expr);
  bool get_index_access_expr(
    const nlohmann::json &expr,
    const nlohmann::json &literal_type,
    exprt &new_expr);
  bool get_index_range_access_expr(
    const nlohmann::json &expr,
    const nlohmann::json &literal_type,
    exprt &new_expr);
  bool get_new_object_expr(
    const nlohmann::json &expr,
    const nlohmann::json &literal_type,
    exprt &new_expr);

  bool get_binary_operator_expr(const nlohmann::json &expr, exprt &new_expr);
  bool get_compound_assign_expr(
    const nlohmann::json &expr,
    exprt &lhs,
    exprt &rhs,
    typet &common_type,
    exprt &new_expr);
  bool get_unary_operator_expr(
    const nlohmann::json &expr,
    const nlohmann::json &literal_type,
    exprt &new_expr);
  bool
  get_conditional_operator_expr(const nlohmann::json &expr, exprt &new_expr);
  bool get_cast_expr(
    const nlohmann::json &cast_expr,
    exprt &new_expr,
    const nlohmann::json literal_type = nullptr);
  bool get_var_decl_ref(
    const nlohmann::json &decl,
    const bool is_this_ptr,
    exprt &new_expr);
  void get_symbol_decl_ref(
    const std::string &sym_name,
    const std::string &sym_id,
    const typet &t,
    exprt &new_expr);
  bool get_func_decl_ref(const nlohmann::json &decl, exprt &new_expr);
  bool get_func_decl_this_ref(const nlohmann::json &decl, exprt &new_expr);
  bool get_func_decl_this_ref(
    const std::string contract_name,
    const std::string &func_id,
    exprt &new_expr);
  bool get_ctor_decl_this_ref(const nlohmann::json &caller, exprt &this_object);
  bool get_ctor_decl_this_ref(const std::string &c_name, exprt &this_object);
  bool get_enum_member_ref(const nlohmann::json &decl, exprt &new_expr);
  bool get_esbmc_builtin_ref(const nlohmann::json &decl, exprt &new_expr);
  bool get_type_description(const nlohmann::json &type_name, typet &new_type);
  bool get_type_description(
    const nlohmann::json &decl,
    const nlohmann::json &type_name,
    typet &new_type);
  bool get_func_decl_ref_type(const nlohmann::json &decl, typet &new_type);
  bool get_array_to_pointer_type(const nlohmann::json &decl, typet &new_type);
  bool
  get_elementary_type_name(const nlohmann::json &type_name, typet &new_type);
  bool get_parameter_list(const nlohmann::json &type_name, typet &new_type);
  void get_state_var_decl_name(
    const nlohmann::json &ast_node,
    const std::string &cname,
    std::string &name,
    std::string &id);
  void get_local_var_decl_name(
    const nlohmann::json &ast_node,
    const std::string &cname,
    std::string &name,
    std::string &id);
  void get_function_definition_name(
    const nlohmann::json &ast_node,
    std::string &name,
    std::string &id);
  void get_error_definition_name(
    const nlohmann::json &ast_node,
    std::string &name,
    std::string &id);
  bool get_var_decl_name(
    const nlohmann::json &decl,
    std::string &name,
    std::string &id);
  bool get_non_library_function_call(
    const nlohmann::json &decl_ref,
    const nlohmann::json &caller,
    side_effect_expr_function_callt &call);
  bool get_super_function_call(
    const nlohmann::json &member_access,
    const nlohmann::json &call_expr,
    exprt &new_expr);
  std::string find_contract_name_for_id(int func_id);
  bool get_ctor_call(
    const nlohmann::json &decl_ref,
    const nlohmann::json &epxr,
    side_effect_expr_function_callt &call);
  bool get_new_object_ctor_call(
    const nlohmann::json &ast_node,
    const bool is_object,
    exprt &new_expr);
  bool get_new_object_ctor_call(
    const std::string &contract_name,
    const nlohmann::json param_list,
    const bool is_object,
    exprt &new_expr);
  void get_current_contract_name(
    const nlohmann::json &ast_node,
    std::string &contract_name);
  bool get_library_function_call(
    const nlohmann::json &decl_ref,
    const nlohmann::json &caller,
    side_effect_expr_function_callt &call,
    bool skip_first_param = false);
  bool get_library_function_call(
    const exprt &func,
    const typet &t,
    const nlohmann::json &decl_ref,
    const nlohmann::json &caller,
    side_effect_expr_function_callt &call,
    bool skip_first_param = false);
  void get_library_function_call_no_args(
    const std::string &func_name,
    const std::string &func_id,
    const typet &t,
    const locationt &l,
    exprt &new_expr);
  void get_malloc_function_call(
    const locationt &loc,
    side_effect_expr_function_callt &_call);
  void get_calloc_function_call(
    const locationt &loc,
    side_effect_expr_function_callt &_call);
  void get_malloc_array_function_call(
    const locationt &loc,
    side_effect_expr_function_callt &_call);
  void get_arrcpy_function_call(
    const locationt &loc,
    side_effect_expr_function_callt &calc_call);
  void get_arrcpy_2d_function_call(
    const locationt &loc,
    side_effect_expr_function_callt &calc_call);
  void get_str_assign_function_call(
    const locationt &loc,
    side_effect_expr_function_callt &_call);
  void get_memcpy_function_call(
    const locationt &loc,
    side_effect_expr_function_callt &_call);
  bool is_esbmc_library_function(const std::string &id);
  bool get_empty_array_ref(const nlohmann::json &ast_node, exprt &new_expr);
  void get_unique_name(
    const std::string &name_prefix,
    const std::string &id_prefix,
    std::string &aux_name,
    std::string &aux_id);
  void get_aux_array_name(std::string &aux_name, std::string &aux_id);
  void
  get_aux_array(const exprt &src_expr, const typet &sub_t, exprt &new_expr);
  void get_aux_var(std::string &aux_name, std::string &aux_id);
  void get_size_expr(const exprt &rhs, exprt &size_expr);
  void store_update_dyn_array(
    const exprt &dyn_arr,
    const exprt &size_expr,
    exprt &store_call);
  bool check_array_push_pop_length(const nlohmann::json &node);

  // tuple
  bool get_tuple_definition(const nlohmann::json &ast_node);
  bool get_tuple_instance(const nlohmann::json &ast_node, exprt &new_expr);
  void get_tuple_name(
    const nlohmann::json &ast_node,
    std::string &name,
    std::string &id);
  bool get_tuple_instance_name(
    const nlohmann::json &ast_node,
    std::string &name,
    std::string &id);
  bool get_tuple_function_ref(const nlohmann::json &ast_node, exprt &new_expr);
  bool get_tuple_member_call(
    const irep_idt instance_id,
    const exprt &comp,
    exprt &new_expr);
  bool construct_tuple_assigments(
    const nlohmann::json &ast_node,
    const exprt &lhs,
    const exprt &rhs);
  bool flatten_nested_tuple_assignment(
    const nlohmann::json &expr,
    const nlohmann::json &lhs_json,
    const nlohmann::json &rhs_json);
  void
  get_tuple_assignment(const nlohmann::json &expr, const exprt &lop, exprt rop);
  void get_tuple_function_call(const exprt &op);
  void get_llc_ret_tuple(symbolt &sym);

  // string
  void
  get_string_assignment(const exprt &lhs, const exprt &rhs, exprt &new_expr);

  // mapping
  void get_mapping_inf_arr_name(
    const std::string &cname,
    const std::string &name,
    std::string &arr_name,
    std::string &arr_id);

  // T1.1 Stage S1: dyn-array per-instance length.  The state-var dynarray
  // length symbol `<arr>_dynarray_len` was a global scalar `uint256`; it is
  // now an addr-keyed infinite array `array_typet(uint256, infinity)`, so
  // every read/write site needs to index by `this->$address`.  This helper
  // computes `len_sym[this->$address]` for the current function or ctor
  // context.  Returns true on failure (no `this` resolvable).
  bool get_dynarr_len_ref(const symbolt &len_sym, exprt &out);

  // T1.1 Stage S2: dyn-array per-instance element index fold.  Builds a
  // call expression `_ESBMC_dynarr_idx(this->$address, pos)` returning a
  // 64-bit slot key.  Used to substitute `pos` in `index_exprt(arr, pos)`
  // for state-var dyn-arrays so two `new C()` instances no longer alias
  // on element reads/writes.  Returns true on failure (no `this`).
  bool get_dynarr_elem_idx(const exprt &pos, exprt &out);

  // Build a `mapping_t` struct initializer expression of the form
  //   { base = &_ESBMC_inf_<cname>_<path_name>[0],
  //     mid  = next_mapping_mid++,
  //     addr = (address_t)addr_owner_this->$address }
  // Lazily creates the `_ESBMC_inf_*[]` global if it doesn't yet exist.
  // Used by both top-level mapping-state-var decls (Block B in
  // get_var_decl) and the Phase-2 ctor walker for struct-internal
  // mapping fields (emit_ctor_deep_init_fixup).
  bool build_mapping_t_init_value(
    const std::string &cname,
    const std::string &path_name,
    const exprt &addr_owner_this,
    const locationt &loc,
    exprt &out_init);
  bool is_mapping_set_lvalue(const nlohmann::json &json);
  bool get_mapping_key_value_type(
    const nlohmann::json &map_node,
    typet &key_t,
    typet &value_t,
    SolidityGrammar::SolType &key_sol_type,
    SolidityGrammar::SolType &val_sol_type);
  void gen_mapping_key_typecast(
    const std::string &c_name,
    exprt &pos,
    const locationt &l,
    const typet &key_type);
  void xor_fold_key_to_64bit(exprt &key);
  // Pack a sequence of already-folded 64-bit mapping keys (outer→inner) into
  // a single uint256 by laying each key into a successive 64-bit lane. Keeps
  // 2-level nested mappings (the common case: balances, allowances, ...)
  // collision-free and lets nested reads/writes share a single linked-list
  // entry in the mapping_t backing store. 3+ levels reuse lower lanes via
  // XOR — collisions degrade to TOD false positives, never crashes.
  void combine_mapping_keys_256(
    const std::vector<exprt> &folded_keys_64,
    exprt &combined);
  bool get_new_mapping_index_access(
    const typet &value_t,
    SolidityGrammar::SolType val_sol_type,
    bool is_mapping_set,
    const exprt &array,
    const exprt &pos,
    const locationt &location,
    exprt &new_expr);
  void get_mapping_struct_function(
    const typet &struct_t,
    std::string &struct_contract_name,
    const side_effect_expr_function_callt &gen_call,
    exprt &new_expr);
  void extract_new_contracts();

  // line number and locations
  void
  get_location_from_node(const nlohmann::json &ast_node, locationt &location);
  void get_start_location_from_stmt(
    const nlohmann::json &ast_node,
    locationt &location);
  void get_final_location_from_stmt(
    const nlohmann::json &ast_node,
    locationt &location);
  unsigned int
  get_line_number(const nlohmann::json &ast_node, bool final_position = false);
  unsigned int add_offset(const std::string &src, unsigned int start_position);
  std::string get_src_from_json(const nlohmann::json &ast_node);

  symbolt *move_symbol_to_context(symbolt &symbol);
  bool multi_transaction_verification(const std::string &contractName);
  bool multi_contract_verification_bound(std::set<std::string> &tgt_set);
  bool multi_contract_verification_unbound(std::set<std::string> &tgt_set);
  // Emit a call to _sol_per_tx_reseed() at the top of a dispatcher
  // while-loop iteration so each iter models a distinct EVM tx with
  // its own sender / value / block context. Always fires — frozen-
  // sender behaviour was itself unsound, so there is no opt-out.
  // Accepts `codet&` (not `code_blockt&`) so it can be reused at
  // call sites that declare `codet while_body` (e.g. site A in
  // multi_transaction_verification) and `code_blockt while_body`
  // (e.g. sites B/C in build_bound_drive_helper /
  // build_esol_state_forward_helper).
  void emit_per_tx_reseed_call(codet &out);
  bool prepare_harness_entry_functions(
    const std::set<std::string> &cname_set,
    std::vector<const symbolt *> &entry_syms);
  bool register_harness_main(const std::string &sol_id, const codet &func_body);
  void reset_auxiliary_vars();

  // auxiliary functions
  std::string get_modulename_from_path(std::string path);
  std::string get_filename_from_path(std::string path);
  void convert_expression_to_code(exprt &expr);
  bool check_intrinsic_function(const nlohmann::json &ast_node);
  nlohmann::json make_implicit_cast_expr(
    const nlohmann::json &sub_expr,
    std::string cast_type);
  nlohmann::json make_return_type_from_typet(typet type);
  nlohmann::json make_pointee_type(const nlohmann::json &sub_expr);
  nlohmann::json make_array_elementary_type(const nlohmann::json &type_descrpt);
  nlohmann::json make_array_to_pointer_type(const nlohmann::json &type_descrpt);
  std::string get_array_size(const nlohmann::json &type_descrpt);
  void get_size_of_expr(const typet &elem_type, exprt &size_of_expr);
  bool is_dyn_array(const nlohmann::json &json_in);
  bool is_func_sig_cover(const std::string &derived, const std::string &base);
  bool is_var_getter_matched(
    const std::string &cname,
    const std::string &tname,
    const typet &ttype);

  void get_default_symbol(
    symbolt &symbol,
    std::string module_name,
    typet type,
    std::string name,
    std::string id,
    locationt location);

  bool get_constant_value(const int ref_id, std::string &value);
  bool get_array_pointer_type(
    const nlohmann::json &decl,
    const typet &base_type,
    typet &new_type);

  // Attempts to build a native nested `array_typet(array_typet(T, inner_N),
  // outer_M)` chain for a multi-dim array whose every dimension is
  // a compile-time fixed size. Returns true and populates `result` on
  // success; returns false when any dimension is dynamic (caller then
  // falls back to the pointer-backed model). Replacement for the
  // `T**` + per-level calloc representation, which triggers a
  // language-agnostic value-set aliasing bug.
  bool try_native_nested_fixed_array(
    const nlohmann::json &type_name_node,
    typet &result);

  bool get_ctor_call_id(const std::string &contract_name, std::string &ctor_id);
  std::string get_explicit_ctor_call_id(const std::string &contract_name);
  std::string get_implict_ctor_call_id(const std::string &contract_name);
  bool get_sol_builtin_ref(const nlohmann::json expr, exprt &new_expr);
  void get_temporary_object(exprt &call, exprt &new_expr);
  bool get_unbound_function(const std::string &c_name, symbolt &sym);
  // Bound-mode helper: builds `_ESBMC_nondet_new_<c_name>()` returning a
  // freshly-allocated instance whose state has been driven through a
  // bounded nondet-dispatch loop (so it represents a reachable Updated
  // State, not just Initial State).  Used from `assign_param_nondet` to
  // populate contract-typed function parameters under `--bound`.
  bool build_bound_drive_helper(const std::string &c_name, symbolt &sym);

  // TOD-mode clone helper: builds `_ESBMC_clone_<c_name>(C *base)`.
  // Returns a freshly-allocated instance whose non-mapping state is a
  // field-by-field copy of *base (so c1 and c2 observe the same pre-
  // race state), but with:
  //   - a fresh nondet $address (distinct from base's, enforced by
  //     __ESBMC_assume) so the two instances do not alias in mapping
  //     keyspace or in equality checks;
  //   - for each `mapping` state variable, the per-instance
  //     `mapping_t.addr` is reset to the clone's new $address, so
  //     subsequent reads/writes via the clone land in a distinct
  //     keyspace (empty by default under the clone's addr — KNOWN
  //     LIMITATION: pre-race mapping state is not mirrored into the
  //     clone's keyspace; follow-up work).
  bool build_tod_clone_helper(const std::string &c_name, symbolt &sym);

  // Recursive per-field fixup for build_tod_clone_helper: after the
  // whole-struct `*c = *base` copy, walks the contract struct components
  // and emits fixups so that pointer-backed fields (fixed-size arrays)
  // are deep-copied and every mapping (including those nested inside
  // user structs) has its `.addr` retargeted to the clone's fresh
  // $address.  Scalars, bytes structs, contract-struct handles are
  // left to the outer struct copy (value-correct for the former,
  // intentional-aliasing-or-out-of-scope for the latter).
  //
  // Parameters:
  //   dst_lvalue:      lvalue expr for the destination field
  //                    (starts at `*c` for the outermost call, nests
  //                     via member_exprt for struct recursion).
  //   src_lvalue:      analogous lvalue on the `base` side.
  //   field_type:      static type of the field (pre-resolution — may
  //                    be a symbol_type that resolves to a struct).
  //   clone_addr_expr: rvalue expr evaluating to the clone's fresh
  //                    $address (used for mapping retargeting).
  //   func_body:       output block — generated code is appended here.
  bool emit_clone_deep_copy_fixup(
    const exprt &dst_lvalue,
    const exprt &src_lvalue,
    const typet &field_type,
    const exprt &clone_addr_expr,
    code_blockt &func_body);

  // Predicate: does `t` contain any pointer-backed fixed array or any
  // mapping_t field at any nesting depth?  Drives the decision in
  // emit_clone_deep_copy_fixup between single-arrcpy (trivial element
  // type) and compile-time-unrolled per-element recursion (non-trivial).
  bool needs_clone_deep_fixup(const typet &t);

  // Phase 2 mirror walker: recursively initialize nested pointer-backed
  // storage in a state-var after its top-level assignment.  The existing
  // state-var decl path only calloc's the OUTERMOST pointer (e.g. for
  // `uint256[M][N] grid`, only the outer N-slot array of pointers is
  // allocated; the inner M-row buffers stay NULL).  For struct-inline
  // fields (e.g. `struct B { uint256[K] cells; } B bx;`), the struct is
  // zero-initialised, leaving `bx.cells == NULL`.  This walker posts
  // inner-level calloc's so the clone walker's per-element arrcpy sees
  // valid source buffers.
  //
  // Called from `move_initializer_to_ctor` right after each state-var
  // assignment, so the sequence becomes:
  //   this->grid  = calloc(N, sizeof(uint256*));        // existing
  //   this->grid[0] = calloc(M, sizeof(uint256));       // walker
  //   this->grid[1] = calloc(M, sizeof(uint256));       // walker
  //   ...
  //   this->bx.cells = calloc(K, sizeof(uint256));      // walker
  // path_name accumulates struct-field names from the top-level state
  // var down to the current sub-field, joined by underscores
  // (e.g. "bx" → "bx_m" → "bx_inner_m").  Used only to disambiguate the
  // `_ESBMC_inf_<C>_<path_name>[]` global emitted for struct-internal
  // mappings; non-mapping branches ignore it.
  bool emit_ctor_deep_init_fixup(
    const exprt &lvalue,
    const typet &field_type,
    code_blockt &out_block,
    const std::string &path_name = "");

  // Predicate: does `t` contain a pointer-backed fixed-size array at
  // any nesting depth?  Gates whether emit_ctor_deep_init_fixup has any
  // work to do.  Scalars, bytes structs, contract handles, and dyn-array
  // state vars (globals, not struct members) never need it.
  bool needs_ctor_deep_init(const typet &t);

  // __ESOL_nondet_state_forward intrinsic helper: builds
  // `_ESBMC_state_forward_<c_name>(C *c)`.  Drives the *supplied*
  // instance in place through a bounded nondet-dispatch loop (no new
  // allocation), so `*c` moves from its current state to some
  // reachable successor state.  Mirrors the while-body shape of
  // build_bound_drive_helper but without the fresh-alloc prologue so
  // users can compose it with a pre-existing pointer.
  bool build_esol_state_forward_helper(
    const std::string &c_name,
    symbolt &sym);
  bool get_unbound_expr(
    const nlohmann::json expr,
    const std::string &cname,
    exprt &new_expr);
  void convert_unboundcall_nondet(
    exprt &new_expr,
    const typet common_type,
    const locationt &l);
  bool add_auxiliary_members(
    const nlohmann::json &json,
    const std::string contract_name);
  void get_builtin_symbol(
    const std::string name,
    const std::string id,
    const typet t,
    const locationt &l,
    const exprt &val,
    const std::string c_name);
  void move_builtin_to_contract(
    const std::string cname,
    const exprt &sym,
    bool is_method);
  void move_builtin_to_contract(
    const std::string cname,
    const exprt &sym,
    const std::string &access,
    bool is_method);
  const nlohmann::json &
  get_func_decl_ref(const std::string &c_name, const std::string &f_name);
  void get_builtin_property_expr(
    const std::string &cname,
    const std::string &name,
    const exprt &base,
    const locationt &loc,
    exprt &new_expr);
  void get_aux_property_function(
    const std::string &cname,
    const exprt &addr,
    const typet &return_t,
    const locationt &loc,
    const std::string &property_name,
    exprt &new_expr);
  void
  get_addr_expr(const std::string &cname, const exprt &base, exprt &new_expr);
  void get_new_object(const typet &t, exprt &this_object);
  bool get_high_level_member_access(
    const nlohmann::json &expr,
    const nlohmann::json &literal_type,
    const exprt &base,
    const exprt &member,
    const exprt &_mem_call,
    const bool is_func_call,
    exprt &new_expr);
  bool get_high_level_member_access(
    const nlohmann::json &expr,
    const exprt &base,
    const exprt &member,
    const exprt &_mem_call,
    const bool is_func_call,
    exprt &new_expr);
  bool get_bound_low_level_call(
    const nlohmann::json &expr,
    const nlohmann::json &literal_type,
    const std::string &mem_name,
    const exprt &base,
    exprt &new_expr);
  bool get_low_level_member_accsss(
    const nlohmann::json &expr,
    const nlohmann::json &options,
    const std::string mem_name,
    const exprt &base,
    const exprt &arg,
    exprt &new_expr);
  bool has_callable_func(const std::string &cname);
  bool
  has_target_function(const std::string &cname, const std::string func_name);
  func_sig
  get_target_function(const std::string &cname, const std::string &func_name);
  bool get_call_definition(
    const std::string &cname,
    exprt &new_expr,
    bool is_library = false);
  bool get_call_value_definition(
    const std::string &cname,
    exprt &new_expr,
    bool is_library = false);
  // Signature-based dispatch for .call(abi.encodeWithSignature(...)).
  // Tries to extract a literal signature + arg list from the payload AST;
  // on success builds an inline typed dispatch helper and fills new_expr.
  // Returns true on failure (caller should fall back to the generic $call#0).
  bool try_get_signature_dispatched_call(
    const nlohmann::json &expr,
    const nlohmann::json &func_call,
    const exprt &base,
    exprt &new_expr);
  // Storage-context shadow dispatch for
  // .delegatecall(abi.encodeWithSignature(...)). Tries to locate the target
  // function in each candidate contract, clone-and-rewrite its body to
  // operate on the caller's state variables, and inline the dispatch chain
  // at the current call site. Returns true on failure (caller should fall
  // back to the generic $delegatecall#0 helper).
  bool try_get_delegate_shadow_call(
    const nlohmann::json &expr,
    const nlohmann::json &func_call,
    const exprt &base,
    exprt &new_expr);
  // Pre-validate that every state-var reference inside target_body resolves
  // by name and type to a state variable of caller_cname. Returns false
  // when every referenced state var has an exact match in the caller.
  bool validate_delegate_shadow_compatible(
    const std::string &caller_cname,
    const nlohmann::json &target_body);
  // Walk an inlined delegate-shadow body and replace every `return X;` with
  // { ret_lvalue = X; goto end_label; } so the return does not escape the
  // enclosing caller function. A `return;` (void) becomes a bare goto.
  void rewrite_returns_for_delegate_shadow(
    exprt &node,
    const exprt &ret_lvalue,
    const std::string &end_label);
  // Attempt to inline a call to an internal helper inside the currently
  // inlined delegate-shadow body.  Returns false on success (new_expr is
  // set to the helper's $dl_ret local or to a skip for void returns, and
  // the inlined body is pushed to front_block via a wrapper).  Returns
  // true on failure so the caller can fall back to the normal call path.
  bool try_inline_delegate_shadow_helper_call(
    const nlohmann::json &call_expr,
    const nlohmann::json &fdecl,
    exprt &new_expr);
  // Parse abi.encodeWithSignature("sig(T1,T2)", a1, a2) AST.
  // Returns false and fills sig_literal + args_out on success.
  bool extract_abi_encode_signature(
    const nlohmann::json &payload,
    std::string &sig_literal,
    std::vector<const nlohmann::json *> &args_out);
  // Build canonical "name(T1,T2,...)" from a FunctionDefinition AST node,
  // using the typeDescriptions.typeString of each parameter (spaces stripped).
  // Returns empty string for ctor/fallback/unnamed or on missing info.
  std::string build_canonical_signature(const nlohmann::json &func_def);
  // Look up a function in a contract by its full canonical signature.
  // Walks src_ast_json to find an external/public FunctionDefinition whose
  // canonical signature matches. Returns empty_json if not found.
  const nlohmann::json &find_function_by_signature(
    const std::string &cname,
    const std::string &target_sig);
  // Lazily generate a per-caller helper that dispatches a typed low-level
  // call to whichever contract's address matches. Helper is registered in
  // the symbol table; out_sym is set to its symbol.
  bool get_typed_call_definition(
    const std::string &caller_cname,
    const std::string &target_sig,
    const std::vector<exprt> &arg_exprs,
    symbolt *&out_sym);
  bool get_transfer_definition(
    const std::string &cname,
    exprt &new_expr,
    bool is_library = false);
  bool get_send_definition(
    const std::string &cname,
    exprt &new_expr,
    bool is_library = false);
  bool get_staticcall_definition(
    const std::string &cname,
    exprt &new_expr,
    bool is_library = false);
  bool get_delegatecall_definition(
    const std::string &cname,
    exprt &new_expr,
    bool is_library = false);
  bool model_transaction(
    const nlohmann::json &expr,
    const exprt &this_expr,
    const exprt &base,
    const exprt &value,
    const locationt &loc,
    exprt &front_block,
    exprt &back_block);

  bool get_bind_cname_expr(const nlohmann::json &json, exprt &bind_cname_expr);
  void get_bind_cname_func_name(
    const std::string &cname,
    std::string &fname,
    std::string &fid);

  /// Get or create a per-pointer `_ESBMC_bind_cname` shadow symbol for a local
  /// contract-typed lvalue.  Because `_ESBMC_Object_<C>` is a global singleton
  /// shared across every `C*` (writes through one pointer are visible through
  /// any other), per-pointer polymorphism cannot be expressed by the struct
  /// field — a shadow symbol carries the per-pointer binding instead.  Used by
  /// the cast path and the mapping-getter polymorphism read path.
  ///
  /// `lvar` must be a local symbol_exprt; returns true (no action) otherwise.
  /// On success, `shadow_out` is a symbol_expr referring to the shadow.
  bool get_or_create_bind_shadow(
    const exprt &lvar,
    const std::string &declared_cname,
    exprt &shadow_out);

  /// Look up the shadow for `base` (if base is a symbol_exprt with a companion
  /// shadow symbol).  Returns true (sets nothing) if no shadow — callers
  /// should then fall back to `member_exprt(base, "_ESBMC_bind_cname", ...)`.
  bool get_bind_shadow_read(const exprt &base, exprt &shadow_out);
  void get_nondet_expr(const typet &t, exprt &new_expr);

  /// T2.4 — Yul precise lowering for inline assembly blocks.
  /// `try_lower_yul_block_precise` walks an `InlineAssembly` AST node and
  /// attempts to lower its `YulBlock` into ESBMC IR with full precision for
  /// the supported subset (let / := / pure-256-bit builtins / if / switch /
  /// for / nested blocks / number+bool literals).  Returns false when the
  /// block contains any unsupported Yul construct; on false, populates
  /// `unsupported_kind` (e.g. "YulFunctionCall:mload" or "YulFunctionDefinition")
  /// and `unsupported_src` (solc source range) so the caller can include them
  /// in the over-approximation warning.  The all-or-nothing rule guarantees
  /// the precise portion can never observe a write the havoc'd portion would
  /// have made.
  bool try_lower_yul_block_precise(
    const nlohmann::json &asm_stmt,
    const locationt &loc,
    exprt &out,
    std::string &unsupported_kind,
    std::string &unsupported_src);

  /// Recursive pre-flight scanner: returns true iff every node in the YulBlock
  /// is in the supported subset.  On false, sets unsupported_kind/_src to the
  /// first offending node.
  bool yul_node_is_supported(
    const nlohmann::json &node,
    std::string &unsupported_kind,
    std::string &unsupported_src);

  /// Lower a YulBlock into a code_blockt.  `locals` is the Yul-name→symbol
  /// environment for `let`-bound vars (mutable; shadowing is handled by
  /// snapshot+restore at block boundaries).  `src_to_decl` maps YulIdentifier
  /// source ranges to outer-scope Solidity VariableDeclaration ids (built
  /// once from externalReferences).  `asm_id` is the InlineAssembly node's id
  /// — used to make Yul-local symbol names unique across multiple assembly
  /// blocks in the same function.
  bool convert_yul_block(
    const nlohmann::json &yul_block,
    const std::string &asm_id,
    const std::map<std::string, int> &src_to_decl,
    std::map<std::string, exprt> &locals,
    int &local_seq,
    const locationt &loc,
    exprt &out);

  bool convert_yul_statement(
    const nlohmann::json &yul_stmt,
    const std::string &asm_id,
    const std::map<std::string, int> &src_to_decl,
    std::map<std::string, exprt> &locals,
    int &local_seq,
    const locationt &loc,
    exprt &out);

  bool convert_yul_expression(
    const nlohmann::json &yul_expr,
    const std::map<std::string, int> &src_to_decl,
    const std::map<std::string, exprt> &locals,
    const locationt &loc,
    exprt &out);

  /// Allocate a fresh Yul-local symbol of type uint256_t scoped to the current
  /// function.  Names are formed from asm_id + a per-block sequence counter so
  /// nested blocks and multiple assembly{} per function never collide.
  bool make_yul_local(
    const std::string &asm_id,
    int seq,
    const std::string &yul_name,
    const locationt &loc,
    exprt &out_sym);

  /// Build the EVM-revert-with-state-rollback replacement for a `revert` /
  /// `require` lowering.  Two output forms based on whether the current
  /// rollback site sits after a state mutation in the same function
  /// body (`current_function_seen_mutation`):
  ///   seen_mutation = true   →  full restore form
  ///     `{ *this = _sol_save_this; return [nondet]; }` (revert)
  ///     `if (!cond) { *this = _sol_save_this; return [nondet]; }` (require)
  ///   seen_mutation = false  →  early-return form
  ///     `{ return [nondet]; }`                              (revert)
  ///     `if (!cond) { return [nondet]; }`                   (require)
  /// Returns true on failure (no current function context, no snapshot
  /// symbol, or unsupported return shape such as a tuple-returning
  /// function), in which case the caller must fall back to the legacy
  /// `__ESBMC_assume(false)` / `__ESBMC_assume(cond)` lowering.
  bool build_revert_rollback_block(const exprt *cond, exprt &out);
  /// Per-statement AST classifier feeding the per-rollback granularity:
  /// returns true when the given top-level body statement is
  /// conservatively state-mutating, false for pure-decls / guards /
  /// return-flow / bare value expressions.  See definition for the
  /// full taxonomy.  Called from get_block after each statement is
  /// converted; OR'd into `current_function_seen_mutation`.
  bool statement_is_mutation_top_level(const nlohmann::json &stmt);
  /// Subroutine of statement_is_mutation_top_level.  Recursively scans
  /// a JSON expression subtree for a FunctionCall whose `kind` is not
  /// `typeConversion` — i.e. an actual function invocation (which we
  /// conservatively treat as a state mutation).  Used to classify the
  /// `initialValue` of a `VariableDeclarationStatement`: a tuple
  /// destructure such as `(bool s,) = addr.call{value:v}("");` has a
  /// FunctionCall RHS even though the LHS is purely local, and the
  /// call may credit/debit balances or otherwise modify state that
  /// must be rolled back if a later require fails.
  bool initializer_has_side_effect(const nlohmann::json &node);

  bool assign_nondet_contract_name(const std::string &_cname, exprt &new_expr);
  bool assign_param_nondet(
    const nlohmann::json &decl_ref,
    side_effect_expr_function_callt &call);
  bool get_base_contract_name(const exprt &base, std::string &cname);

  // literal conversion functions
  bool convert_integer_literal(
    const nlohmann::json &integer_literal,
    std::string the_value,
    exprt &dest);
  bool convert_bool_literal(
    const nlohmann::json &bool_literal,
    std::string the_value,
    exprt &dest);
  bool convert_string_literal(std::string the_value, exprt &dest);
  void convert_type_expr(
    const namespacet &ns,
    exprt &src_expr,
    const typet &dest_type,
    const nlohmann::json &expr);
  void convert_type_expr(
    const namespacet &ns,
    exprt &src_expr,
    const exprt &dest_expr,
    const nlohmann::json &expr);
  bool
  convert_hex_literal(std::string the_value, exprt &dest, const int n = 256);
  // check if it's a bytes type
  bool is_byte_type(const typet &t);
  bool is_bytes_type(const typet &t);
  bool is_bytesN_type(const typet &t);
  exprt make_aux_var(exprt &val, const locationt &location);
  void get_bytesN_size(const exprt &src_expr, exprt &len_expr);
  bool has_contract_bytes(const nlohmann::json &json);
  bool get_dynamic_pool(const std::string &c_name, exprt &pool);
  bool get_dynamic_pool(const nlohmann::json &expr, exprt &pool);

  contextt &context;
  namespacet ns;
  // json for Solidity AST. Use vector for multiple contracts
  nlohmann::json src_ast_json_array = nlohmann::json::array();
  // Solidity contracts/ function to be verified
  const std::string &tgt_cnts;
  std::set<std::string> tgt_cnt_set;
  const std::string &tgt_func;
  // focus function: when set, the harness for the target contract
  // dispatches only this public/external function (constructor + state
  // init still run). Empty means feature disabled.
  std::string focus_func;
  //smart contract source file
  const std::string &contract_path;

  std::string absolute_path;
  std::string contract_contents = "";

  const nlohmann::json *current_functionDecl;
  const nlohmann::json *current_forStmt;
  // store multiple exprt and flatten the block
  code_blockt expr_frontBlockDecl;
  code_blockt expr_backBlockDecl;
  code_blockt ctor_frontBlockDecl;
  code_blockt ctor_backBlockDecl;
  // for tuple
  bool current_lhsDecl;
  bool current_rhsDecl;
  // Use current level of BinOp type as the "anchor" type for numerical literal conversion:
  // In order to remove the unnecessary implicit IntegralCast. We need type of current level of BinaryOperator.
  // All numeric literals will be implicitly converted to this type. Pop it when finishing the current level of BinaryOperator.
  // TODO: find a better way to deal with implicit type casting if it's not able to cope with complex rules
  std::stack<const nlohmann::json *> current_BinOp_type;
  std::string current_functionName;
  // Mirror of the symbol-table id of the function currently being lowered
  // (i.e., the same `id` that get_function_definition assigns to the symbol).
  // Used by build_revert_rollback_block() to locate the per-function
  // `_sol_save_this` snapshot symbol from inside an expression-level lowering.
  std::string current_functionId;
  // Set to true by build_revert_rollback_block() when it emits the
  // full restore form (`*this = save; return`).  After body
  // conversion, get_function_definition uses this flag to gate the
  // emission of the `_sol_save_this` decl + init at function entry —
  // so that functions whose require/revert sites all lower to the
  // early-return form (because no state mutation precedes them) pay
  // no SSA cost for the snapshot copy.  Reset on entry to each
  // function.
  bool current_function_used_snapshot = false;
  // Forward-incremental flag updated by get_block as it walks the
  // body's top-level statements: ORed with `statement_is_mutation_top_level(stmt)`
  // after each statement is converted.  build_revert_rollback_block
  // reads this flag to decide whether the rollback at the current
  // site needs to restore `*this` (mutation has been observed
  // earlier in the body — emit `*this = save; return [nondet]`) or
  // can lower to plain early-return (no mutation yet — emit just
  // `return [nondet]`).  Per-rollback granularity replaces the v2
  // function-level decision so that a function with mixed
  // pre/post-mutation guards only pays the snapshot SSA cost on
  // the post-mutation guards.  Reset on entry to each function.
  bool current_function_seen_mutation = false;
  // Track whether we are inside a Solidity "unchecked { ... }" block.
  // When true, arithmetic overflow checks should be suppressed.
  bool in_unchecked_block = false;

  // Auxiliary data structures:
  // Inheritance Order Record <contract_name, Contract_id>
  std::unordered_map<std::string, std::vector<int>> linearizedBaseList;
  // Who inherits from me?
  std::unordered_map<std::string, std::unordered_set<std::string>>
    inheritanceMap;
  // structural typing indentical
  std::unordered_map<std::string, std::unordered_set<std::string>>
    structureTypingMap;
  // virtual-override
  std::unordered_map<std::string, std::unordered_map<int, int>> overrideMap;
  // Storage reference aliases: maps a local storage variable's AST id
  // to the AST id of its source (e.g. secondWrapper → wrapper).
  std::unordered_map<int, int> storage_ref_aliases;
  // Expression-based storage ref aliases: for storage refs initialized from
  // complex expressions (e.g. campaigns[0]), stores the initializer JSON
  // so uses can be resolved by re-evaluating the expression.
  std::unordered_map<int, nlohmann::json> storage_ref_expr_aliases;

  // Delegate-shadow parameter remap: when inlining a target function body
  // at a .delegatecall(...) call site, references to the target function's
  // formal parameters (looked up by AST id) are redirected to pre-declared
  // local variables in the caller's scope. Cleared after each inline.
  std::unordered_map<int, std::string> delegate_shadow_param_remap;
  // When non-null, overrides `current_functionDecl["returnParameters"]` for
  // return-statement conversion. Set while inlining a target function body
  // into the caller's context so that `return X;` picks up the target's
  // return type, not the caller's (which may be void).
  const nlohmann::json *delegate_shadow_target_return_params = nullptr;
  // Name of the target contract currently being inlined by the delegate
  // shadow path. When non-empty, function calls inside the inlined body
  // that target internal/private functions of this contract are inlined
  // recursively instead of going through the normal call-path (which
  // would emit a `(TargetContract*)this` cast and silently depend on
  // struct layout coincidences).
  std::string delegate_shadow_target_cname;

  // contract name list
  std::unordered_map<int, std::string> contractNamesMap;
  std::vector<std::string> contractNamesList;
  // for Library/Interface/Abstract Contract
  std::set<std::string> nonContractNamesList;
  // for mapping hack
  std::set<std::string> newContractSet;
  // Store the ast_node["id"] of struct/error
  // where entity contains "members": [{}, {}...]
  std::unordered_map<int, std::string> member_entity_scope;
  // Store state variables
  code_blockt initializers;

  static constexpr const char *mode = "C++";

  std::unordered_map<std::string, std::vector<func_sig>> funcSignatures;

  // The prefix for the id of each class (Solidity-defined structs)
  std::string prefix = "tag-";

  // The prefix for c2goto library struct types (C frontend uses "struct" in tag)
  std::string lib_prefix = "tag-struct ";

  // for auxiliary var name
  int aux_counter;

  // Per-state-var unique ID for the mapping `mid` field.  Each mapping
  // declaration gets the next value here.  Bumps from 1 so that mid==0
  // can sentinel "uninitialised mapping" if needed.  See Stage 3 of
  // /home/samson/.claude/plans/foamy-sniffing-toucan.md and
  // src/c2goto/library/solidity/solidity_mapping.c for the runtime side.
  uint64_t next_mapping_mid = 1;

  // Per-interface/contract unique ID for `type(I).interfaceId`,
  // `type(C).creationCode`, `type(C).runtimeCode`. Each distinct name
  // gets the next value here. Same name → same ID → same library
  // helper output (post-fix: `~id`, bijective on uint32). Closes
  // ledger #15 — pre-fix the helpers ignored their context and
  // returned fresh nondets per call, breaking real-EVM stability.
  std::map<std::string, uint32_t> interface_id_table;
  uint32_t next_interface_id = 1;

  // T1.1 Stage S3: per-contract list of state-var dyn-arrays so the clone
  // helper can iterate them and emit per-instance length+element copy at
  // clone time.  Key: contract name. Value: list of (var symbol-id,
  // element type).  Populated during get_var_decl when is_dynarray_state
  // fires.  Inherited dyn-arrays appear under each derived contract that
  // re-processes them via merge_inheritance_ast.
  std::map<std::string,
           std::vector<std::pair<std::string, typet>>>
    dynarray_state_vars;

  // bound setting
  bool is_bound;

  // Check if a contract should use "new" expression semantics (dynamic allocation).
  // In unbound mode with a single verification target, new-expressions are optimized
  // away (treated as static instances) to reduce state space.
  bool should_treat_as_new(const std::string &contract_name) const
  {
    if (!newContractSet.count(contract_name))
      return false;
    if (
      !is_bound && tgt_cnt_set.count(contract_name) > 0 &&
      tgt_cnt_set.size() == 1)
      return false;
    return true;
  }

  // Check whether a VariableDeclaration JSON was initialized via `new C()`.
  // Returns true when the decl has `value: { nodeType: "FunctionCall",
  //   expression: { nodeType: "NewExpression" } }`.
  // Only handles state-variable declarations (where the init lives directly
  // on the VariableDeclaration).  For locals use is_new_created_decl(id).
  static bool is_new_created_var(const nlohmann::json &var_decl)
  {
    if (!var_decl.contains("value"))
      return false;
    const auto &val = var_decl["value"];
    return val.value("nodeType", "") == "FunctionCall" && val.contains("expression") &&
           val["expression"].value("nodeType", "") == "NewExpression";
  }

  // Same intent as is_new_created_var but works for both state-variable
  // declarations (init is on the VariableDeclaration's `value` field) AND
  // local-variable declarations (init lives on the parent
  // VariableDeclarationStatement's `initialValue`, with the var listed in
  // `declarations`).  Walks src_ast_json to find the parent statement.
  bool is_new_created_decl(int decl_id) const;

  // reentry-check setting
  bool is_reentry_check;

  // pointer-check setting
  bool is_pointer_check;

  // NONDET
  side_effect_expr_function_callt nondet_bool_expr;
  side_effect_expr_function_callt nondet_uint_expr;       // 32-bit
  side_effect_expr_function_callt nondet_uint256_expr;    // 256-bit, for $balance/$codehash/$code/etc.
  side_effect_expr_function_callt nondet_bytes_dynamic_expr;

  // type
  typet addr_t;
  typet addrp_t;
  typet string_t;
  typet bool_t;
  typet byte_dynamic_t;
  typet byte_static_t;

private:
  bool get_elementary_type_name_uint(
    SolidityGrammar::ElementaryTypeNameT &type,
    typet &out);
  bool get_elementary_type_name_int(
    SolidityGrammar::ElementaryTypeNameT &type,
    typet &out);
  bool get_elementary_type_name_bytesn(
    SolidityGrammar::ElementaryTypeNameT &type,
    typet &out);

  // RAII scope guards for global state variables.
  // Usage: ScopeGuard<T> guard(member, new_value);
  // Restores original value on destruction (including early returns/exceptions).
  template <typename T>
  class ScopeGuard
  {
    T &ref;
    T saved;

  public:
    ScopeGuard(T &target, const T &new_val) : ref(target), saved(target)
    {
      ref = new_val;
    }
    ~ScopeGuard()
    {
      ref = saved;
    }
    ScopeGuard(const ScopeGuard &) = delete;
    ScopeGuard &operator=(const ScopeGuard &) = delete;
  };

  // Stack push guard: pushes on construction, pops on destruction.
  template <typename T>
  class StackGuard
  {
    std::stack<T> &stk;

  public:
    StackGuard(std::stack<T> &s, const T &val) : stk(s)
    {
      stk.push(val);
    }
    ~StackGuard()
    {
      stk.pop();
    }
    StackGuard(const StackGuard &) = delete;
    StackGuard &operator=(const StackGuard &) = delete;
  };
};

static inline void static_lifetime_init(const contextt &context, codet &dest)
{
  dest = code_blockt();

  // call designated "initialization" functions
  context.foreach_operand_in_order([&dest](const symbolt &s) {
    if (s.type.initialization() && s.type.is_code())
    {
      code_function_callt function_call;
      function_call.function() = symbol_expr(s);
      dest.move_to_operands(function_call);
    }
  });
}

#endif /* SOLIDITY_FRONTEND_SOLIDITY_CONVERT_H_ */
