#include <map>
#include <unordered_map>
#include <bitwuzla_conv.h>
#include <solvers/solve.h>
#include <cstring>
#include <cstdio>

#define new_ast new_solver_ast<bitw_smt_ast>

void bitwuzla_error_handler(const char *msg)
{
  log_error("Bitwuzla error encountered\n{}", msg);
  abort();
}

smt_convt *create_new_bitwuzla_solver(
  const optionst &options,
  const namespacet &ns,
  tuple_iface **tuple_api [[maybe_unused]],
  array_iface **array_api,
  fp_convt **fp_api)
{
  bitwuzla_convt *conv = new bitwuzla_convt(ns, options);
  *array_api = static_cast<array_iface *>(conv);
  *fp_api = static_cast<fp_convt *>(conv);
  return conv;
}

bitwuzla_convt::bitwuzla_convt(const namespacet &ns, const optionst &options)
  : smt_convt(ns, options), array_iface(true, true), fp_convt(this)
{
  if (options.get_bool_option("int-encoding"))
  {
    log_error("Bitwuzla does not support integer encoding mode");
    abort();
  }

  bitw_options = bitwuzla_options_new();
  bitw_term_manager = bitwuzla_term_manager_new();
  bitwuzla_set_option(bitw_options, BITWUZLA_OPT_PRODUCE_MODELS, 1);
  // Per-query budget (--path-cov-claim-timeout). Set on bitw_options BEFORE
  // bitwuzla_new, which is the only point at which options are consumed. The
  // limit is per satisfiability check and 0 disables it, which matches the
  // option's own "0 = unlimited" exactly, so no special case is needed.
  if (const uint64_t ms = smt_per_query_timeout_ms(options))
  {
    bitwuzla_set_option(bitw_options, BITWUZLA_OPT_TIME_LIMIT_PER, ms);
    smt_record_timeout_mechanism(
      "bitwuzla: native BITWUZLA_OPT_TIME_LIMIT_PER (per satisfiability "
      "check, milliseconds)");
  }
  bitwuzla_set_abort_callback(bitwuzla_error_handler);
  bitw = bitwuzla_new(bitw_term_manager, bitw_options);
}

bitwuzla_convt::~bitwuzla_convt()
{
  bitwuzla_delete(bitw);
  bitwuzla_options_delete(bitw_options);
  bitwuzla_term_manager_delete(bitw_term_manager);
  bitw = nullptr;
  bitw_options = nullptr;
}

void bitwuzla_convt::push_ctx()
{
  smt_convt::push_ctx();
  bitwuzla_push(bitw, 1);
}

void bitwuzla_convt::pop_ctx()
{
  symtabt::nth_index<1>::type &symtab_levels = symtable.get<1>();
  symtab_levels.erase(ctx_level);

  bitwuzla_pop(bitw, 1);
  smt_convt::pop_ctx();
}

smt_convt::resultt bitwuzla_convt::dec_solve()
{
  pre_solve();

  BitwuzlaResult result = bitwuzla_check_sat(bitw);

  if (result == BITWUZLA_SAT)
    return P_SATISFIABLE;

  if (result == BITWUZLA_UNSAT)
    return P_UNSATISFIABLE;

  if (const char *d = getenv("ESBMC_BZLA_DUMP_UNKNOWN"))
  {
    FILE *f = fopen(d, "w");
    if (f)
    {
      bitwuzla_print_formula(bitw, "smt2", f, 10);
      fclose(f);
    }
  }
  return P_ERROR;
}

const std::string bitwuzla_convt::solver_text()
{
  std::string ss = "Bitwuzla ";
  ss += bitwuzla_version();
  return ss;
}

void bitwuzla_convt::assert_ast(smt_astt a)
{
  bitwuzla_assert(bitw, to_solver_smt_ast<bitw_smt_ast>(a)->a);
}

smt_astt bitwuzla_convt::mk_bvadd(smt_astt a, smt_astt b)
{
  assert(a->sort->id != SMT_SORT_INT && a->sort->id != SMT_SORT_REAL);
  assert(b->sort->id != SMT_SORT_INT && b->sort->id != SMT_SORT_REAL);
  assert(a->sort->get_data_width() == b->sort->get_data_width());
  return new_ast(
    bitwuzla_mk_term2(
      bitw_term_manager,
      BITWUZLA_KIND_BV_ADD,
      to_solver_smt_ast<bitw_smt_ast>(a)->a,
      to_solver_smt_ast<bitw_smt_ast>(b)->a),
    a->sort);
}

smt_astt bitwuzla_convt::mk_bvsub(smt_astt a, smt_astt b)
{
  assert(a->sort->id != SMT_SORT_INT && a->sort->id != SMT_SORT_REAL);
  assert(b->sort->id != SMT_SORT_INT && b->sort->id != SMT_SORT_REAL);
  assert(a->sort->get_data_width() == b->sort->get_data_width());
  return new_ast(
    bitwuzla_mk_term2(
      bitw_term_manager,
      BITWUZLA_KIND_BV_SUB,
      to_solver_smt_ast<bitw_smt_ast>(a)->a,
      to_solver_smt_ast<bitw_smt_ast>(b)->a),
    a->sort);
}

smt_astt bitwuzla_convt::mk_bvmul(smt_astt a, smt_astt b)
{
  assert(a->sort->id != SMT_SORT_INT && a->sort->id != SMT_SORT_REAL);
  assert(b->sort->id != SMT_SORT_INT && b->sort->id != SMT_SORT_REAL);
  assert(a->sort->get_data_width() == b->sort->get_data_width());
  return new_ast(
    bitwuzla_mk_term2(
      bitw_term_manager,
      BITWUZLA_KIND_BV_MUL,
      to_solver_smt_ast<bitw_smt_ast>(a)->a,
      to_solver_smt_ast<bitw_smt_ast>(b)->a),
    a->sort);
}

smt_astt bitwuzla_convt::mk_bvsmod(smt_astt a, smt_astt b)
{
  assert(a->sort->id != SMT_SORT_INT && a->sort->id != SMT_SORT_REAL);
  assert(b->sort->id != SMT_SORT_INT && b->sort->id != SMT_SORT_REAL);
  assert(a->sort->get_data_width() == b->sort->get_data_width());
  return new_ast(
    bitwuzla_mk_term2(
      bitw_term_manager,
      BITWUZLA_KIND_BV_SREM,
      to_solver_smt_ast<bitw_smt_ast>(a)->a,
      to_solver_smt_ast<bitw_smt_ast>(b)->a),
    a->sort);
}

smt_astt bitwuzla_convt::mk_bvumod(smt_astt a, smt_astt b)
{
  assert(a->sort->id != SMT_SORT_INT && a->sort->id != SMT_SORT_REAL);
  assert(b->sort->id != SMT_SORT_INT && b->sort->id != SMT_SORT_REAL);
  assert(a->sort->get_data_width() == b->sort->get_data_width());
  return new_ast(
    bitwuzla_mk_term2(
      bitw_term_manager,
      BITWUZLA_KIND_BV_UREM,
      to_solver_smt_ast<bitw_smt_ast>(a)->a,
      to_solver_smt_ast<bitw_smt_ast>(b)->a),
    a->sort);
}

smt_astt bitwuzla_convt::mk_bvsdiv(smt_astt a, smt_astt b)
{
  assert(a->sort->id != SMT_SORT_INT && a->sort->id != SMT_SORT_REAL);
  assert(b->sort->id != SMT_SORT_INT && b->sort->id != SMT_SORT_REAL);
  assert(a->sort->get_data_width() == b->sort->get_data_width());
  return new_ast(
    bitwuzla_mk_term2(
      bitw_term_manager,
      BITWUZLA_KIND_BV_SDIV,
      to_solver_smt_ast<bitw_smt_ast>(a)->a,
      to_solver_smt_ast<bitw_smt_ast>(b)->a),
    a->sort);
}

smt_astt bitwuzla_convt::mk_bvudiv(smt_astt a, smt_astt b)
{
  assert(a->sort->id != SMT_SORT_INT && a->sort->id != SMT_SORT_REAL);
  assert(b->sort->id != SMT_SORT_INT && b->sort->id != SMT_SORT_REAL);
  assert(a->sort->get_data_width() == b->sort->get_data_width());
  return new_ast(
    bitwuzla_mk_term2(
      bitw_term_manager,
      BITWUZLA_KIND_BV_UDIV,
      to_solver_smt_ast<bitw_smt_ast>(a)->a,
      to_solver_smt_ast<bitw_smt_ast>(b)->a),
    a->sort);
}

smt_astt bitwuzla_convt::mk_bvshl(smt_astt a, smt_astt b)
{
  assert(a->sort->id != SMT_SORT_INT && a->sort->id != SMT_SORT_REAL);
  assert(b->sort->id != SMT_SORT_INT && b->sort->id != SMT_SORT_REAL);
  assert(a->sort->get_data_width() == b->sort->get_data_width());
  return new_ast(
    bitwuzla_mk_term2(
      bitw_term_manager,
      BITWUZLA_KIND_BV_SHL,
      to_solver_smt_ast<bitw_smt_ast>(a)->a,
      to_solver_smt_ast<bitw_smt_ast>(b)->a),
    a->sort);
}

smt_astt bitwuzla_convt::mk_bvashr(smt_astt a, smt_astt b)
{
  assert(a->sort->id != SMT_SORT_INT && a->sort->id != SMT_SORT_REAL);
  assert(b->sort->id != SMT_SORT_INT && b->sort->id != SMT_SORT_REAL);
  assert(a->sort->get_data_width() == b->sort->get_data_width());
  return new_ast(
    bitwuzla_mk_term2(
      bitw_term_manager,
      BITWUZLA_KIND_BV_ASHR,
      to_solver_smt_ast<bitw_smt_ast>(a)->a,
      to_solver_smt_ast<bitw_smt_ast>(b)->a),
    a->sort);
}

smt_astt bitwuzla_convt::mk_bvlshr(smt_astt a, smt_astt b)
{
  assert(a->sort->id != SMT_SORT_INT && a->sort->id != SMT_SORT_REAL);
  assert(b->sort->id != SMT_SORT_INT && b->sort->id != SMT_SORT_REAL);
  assert(a->sort->get_data_width() == b->sort->get_data_width());
  return new_ast(
    bitwuzla_mk_term2(
      bitw_term_manager,
      BITWUZLA_KIND_BV_SHR,
      to_solver_smt_ast<bitw_smt_ast>(a)->a,
      to_solver_smt_ast<bitw_smt_ast>(b)->a),
    a->sort);
}

smt_astt bitwuzla_convt::mk_bvneg(smt_astt a)
{
  assert(a->sort->id != SMT_SORT_INT && a->sort->id != SMT_SORT_REAL);
  return new_ast(
    bitwuzla_mk_term1(
      bitw_term_manager,
      BITWUZLA_KIND_BV_NEG,
      to_solver_smt_ast<bitw_smt_ast>(a)->a),
    a->sort);
}

smt_astt bitwuzla_convt::mk_bvnot(smt_astt a)
{
  assert(a->sort->id != SMT_SORT_INT && a->sort->id != SMT_SORT_REAL);
  return new_ast(
    bitwuzla_mk_term1(
      bitw_term_manager,
      BITWUZLA_KIND_BV_NOT,
      to_solver_smt_ast<bitw_smt_ast>(a)->a),
    a->sort);
}

smt_astt bitwuzla_convt::mk_bvnor(smt_astt a, smt_astt b)
{
  assert(a->sort->id != SMT_SORT_INT && a->sort->id != SMT_SORT_REAL);
  assert(b->sort->id != SMT_SORT_INT && b->sort->id != SMT_SORT_REAL);
  assert(a->sort->get_data_width() == b->sort->get_data_width());
  return new_ast(
    bitwuzla_mk_term2(
      bitw_term_manager,
      BITWUZLA_KIND_BV_NOR,
      to_solver_smt_ast<bitw_smt_ast>(a)->a,
      to_solver_smt_ast<bitw_smt_ast>(b)->a),
    a->sort);
}

smt_astt bitwuzla_convt::mk_bvnand(smt_astt a, smt_astt b)
{
  assert(a->sort->id != SMT_SORT_INT && a->sort->id != SMT_SORT_REAL);
  assert(b->sort->id != SMT_SORT_INT && b->sort->id != SMT_SORT_REAL);
  assert(a->sort->get_data_width() == b->sort->get_data_width());
  return new_ast(
    bitwuzla_mk_term2(
      bitw_term_manager,
      BITWUZLA_KIND_BV_NAND,
      to_solver_smt_ast<bitw_smt_ast>(a)->a,
      to_solver_smt_ast<bitw_smt_ast>(b)->a),
    a->sort);
}

smt_astt bitwuzla_convt::mk_bvxor(smt_astt a, smt_astt b)
{
  assert(a->sort->id != SMT_SORT_INT && a->sort->id != SMT_SORT_REAL);
  assert(b->sort->id != SMT_SORT_INT && b->sort->id != SMT_SORT_REAL);
  assert(a->sort->get_data_width() == b->sort->get_data_width());
  return new_ast(
    bitwuzla_mk_term2(
      bitw_term_manager,
      BITWUZLA_KIND_BV_XOR,
      to_solver_smt_ast<bitw_smt_ast>(a)->a,
      to_solver_smt_ast<bitw_smt_ast>(b)->a),
    a->sort);
}

smt_astt bitwuzla_convt::mk_bvor(smt_astt a, smt_astt b)
{
  assert(a->sort->id != SMT_SORT_INT && a->sort->id != SMT_SORT_REAL);
  assert(b->sort->id != SMT_SORT_INT && b->sort->id != SMT_SORT_REAL);
  assert(a->sort->get_data_width() == b->sort->get_data_width());
  return new_ast(
    bitwuzla_mk_term2(
      bitw_term_manager,
      BITWUZLA_KIND_BV_OR,
      to_solver_smt_ast<bitw_smt_ast>(a)->a,
      to_solver_smt_ast<bitw_smt_ast>(b)->a),
    a->sort);
}

smt_astt bitwuzla_convt::mk_bvand(smt_astt a, smt_astt b)
{
  assert(a->sort->id != SMT_SORT_INT && a->sort->id != SMT_SORT_REAL);
  assert(b->sort->id != SMT_SORT_INT && b->sort->id != SMT_SORT_REAL);
  assert(a->sort->get_data_width() == b->sort->get_data_width());
  return new_ast(
    bitwuzla_mk_term2(
      bitw_term_manager,
      BITWUZLA_KIND_BV_AND,
      to_solver_smt_ast<bitw_smt_ast>(a)->a,
      to_solver_smt_ast<bitw_smt_ast>(b)->a),
    a->sort);
}

smt_astt bitwuzla_convt::mk_implies(smt_astt a, smt_astt b)
{
  assert(a->sort->id == SMT_SORT_BOOL && b->sort->id == SMT_SORT_BOOL);
  return new_ast(
    bitwuzla_mk_term2(
      bitw_term_manager,
      BITWUZLA_KIND_IMPLIES,
      to_solver_smt_ast<bitw_smt_ast>(a)->a,
      to_solver_smt_ast<bitw_smt_ast>(b)->a),
    boolean_sort);
}

smt_astt bitwuzla_convt::mk_xor(smt_astt a, smt_astt b)
{
  assert(a->sort->id == SMT_SORT_BOOL && b->sort->id == SMT_SORT_BOOL);
  return new_ast(
    bitwuzla_mk_term2(
      bitw_term_manager,
      BITWUZLA_KIND_XOR,
      to_solver_smt_ast<bitw_smt_ast>(a)->a,
      to_solver_smt_ast<bitw_smt_ast>(b)->a),
    boolean_sort);
}

smt_astt bitwuzla_convt::mk_or(smt_astt a, smt_astt b)
{
  assert(a->sort->id == SMT_SORT_BOOL && b->sort->id == SMT_SORT_BOOL);
  return new_ast(
    bitwuzla_mk_term2(
      bitw_term_manager,
      BITWUZLA_KIND_OR,
      to_solver_smt_ast<bitw_smt_ast>(a)->a,
      to_solver_smt_ast<bitw_smt_ast>(b)->a),
    boolean_sort);
}

smt_astt bitwuzla_convt::mk_and(smt_astt a, smt_astt b)
{
  assert(a->sort->id == SMT_SORT_BOOL && b->sort->id == SMT_SORT_BOOL);
  return new_ast(
    bitwuzla_mk_term2(
      bitw_term_manager,
      BITWUZLA_KIND_AND,
      to_solver_smt_ast<bitw_smt_ast>(a)->a,
      to_solver_smt_ast<bitw_smt_ast>(b)->a),
    boolean_sort);
}

smt_astt bitwuzla_convt::mk_not(smt_astt a)
{
  assert(a->sort->id == SMT_SORT_BOOL);
  return new_ast(
    bitwuzla_mk_term1(
      bitw_term_manager,
      BITWUZLA_KIND_NOT,
      to_solver_smt_ast<bitw_smt_ast>(a)->a),
    boolean_sort);
}

smt_astt bitwuzla_convt::mk_bvult(smt_astt a, smt_astt b)
{
  assert(a->sort->id != SMT_SORT_INT && a->sort->id != SMT_SORT_REAL);
  assert(b->sort->id != SMT_SORT_INT && b->sort->id != SMT_SORT_REAL);
  assert(a->sort->get_data_width() == b->sort->get_data_width());
  return new_ast(
    bitwuzla_mk_term2(
      bitw_term_manager,
      BITWUZLA_KIND_BV_ULT,
      to_solver_smt_ast<bitw_smt_ast>(a)->a,
      to_solver_smt_ast<bitw_smt_ast>(b)->a),
    boolean_sort);
}

smt_astt bitwuzla_convt::mk_bvslt(smt_astt a, smt_astt b)
{
  assert(a->sort->id != SMT_SORT_INT && a->sort->id != SMT_SORT_REAL);
  assert(b->sort->id != SMT_SORT_INT && b->sort->id != SMT_SORT_REAL);
  assert(a->sort->get_data_width() == b->sort->get_data_width());
  return new_ast(
    bitwuzla_mk_term2(
      bitw_term_manager,
      BITWUZLA_KIND_BV_SLT,
      to_solver_smt_ast<bitw_smt_ast>(a)->a,
      to_solver_smt_ast<bitw_smt_ast>(b)->a),
    boolean_sort);
}

smt_astt bitwuzla_convt::mk_bvugt(smt_astt a, smt_astt b)
{
  assert(a->sort->id != SMT_SORT_INT && a->sort->id != SMT_SORT_REAL);
  assert(b->sort->id != SMT_SORT_INT && b->sort->id != SMT_SORT_REAL);
  assert(a->sort->get_data_width() == b->sort->get_data_width());
  return new_ast(
    bitwuzla_mk_term2(
      bitw_term_manager,
      BITWUZLA_KIND_BV_UGT,
      to_solver_smt_ast<bitw_smt_ast>(a)->a,
      to_solver_smt_ast<bitw_smt_ast>(b)->a),
    boolean_sort);
}

smt_astt bitwuzla_convt::mk_bvsgt(smt_astt a, smt_astt b)
{
  assert(a->sort->id != SMT_SORT_INT && a->sort->id != SMT_SORT_REAL);
  assert(b->sort->id != SMT_SORT_INT && b->sort->id != SMT_SORT_REAL);
  assert(a->sort->get_data_width() == b->sort->get_data_width());
  return new_ast(
    bitwuzla_mk_term2(
      bitw_term_manager,
      BITWUZLA_KIND_BV_SGT,
      to_solver_smt_ast<bitw_smt_ast>(a)->a,
      to_solver_smt_ast<bitw_smt_ast>(b)->a),
    boolean_sort);
}

smt_astt bitwuzla_convt::mk_bvule(smt_astt a, smt_astt b)
{
  assert(a->sort->id != SMT_SORT_INT && a->sort->id != SMT_SORT_REAL);
  assert(b->sort->id != SMT_SORT_INT && b->sort->id != SMT_SORT_REAL);
  assert(a->sort->get_data_width() == b->sort->get_data_width());
  return new_ast(
    bitwuzla_mk_term2(
      bitw_term_manager,
      BITWUZLA_KIND_BV_ULE,
      to_solver_smt_ast<bitw_smt_ast>(a)->a,
      to_solver_smt_ast<bitw_smt_ast>(b)->a),
    boolean_sort);
}

smt_astt bitwuzla_convt::mk_bvsle(smt_astt a, smt_astt b)
{
  assert(a->sort->id != SMT_SORT_INT && a->sort->id != SMT_SORT_REAL);
  assert(b->sort->id != SMT_SORT_INT && b->sort->id != SMT_SORT_REAL);
  assert(a->sort->get_data_width() == b->sort->get_data_width());
  return new_ast(
    bitwuzla_mk_term2(
      bitw_term_manager,
      BITWUZLA_KIND_BV_SLE,
      to_solver_smt_ast<bitw_smt_ast>(a)->a,
      to_solver_smt_ast<bitw_smt_ast>(b)->a),
    boolean_sort);
}

smt_astt bitwuzla_convt::mk_bvuge(smt_astt a, smt_astt b)
{
  assert(a->sort->id != SMT_SORT_INT && a->sort->id != SMT_SORT_REAL);
  assert(b->sort->id != SMT_SORT_INT && b->sort->id != SMT_SORT_REAL);
  assert(a->sort->get_data_width() == b->sort->get_data_width());
  return new_ast(
    bitwuzla_mk_term2(
      bitw_term_manager,
      BITWUZLA_KIND_BV_UGE,
      to_solver_smt_ast<bitw_smt_ast>(a)->a,
      to_solver_smt_ast<bitw_smt_ast>(b)->a),
    boolean_sort);
}

smt_astt bitwuzla_convt::mk_bvsge(smt_astt a, smt_astt b)
{
  assert(a->sort->id != SMT_SORT_INT && a->sort->id != SMT_SORT_REAL);
  assert(b->sort->id != SMT_SORT_INT && b->sort->id != SMT_SORT_REAL);
  assert(a->sort->get_data_width() == b->sort->get_data_width());
  return new_ast(
    bitwuzla_mk_term2(
      bitw_term_manager,
      BITWUZLA_KIND_BV_SGE,
      to_solver_smt_ast<bitw_smt_ast>(a)->a,
      to_solver_smt_ast<bitw_smt_ast>(b)->a),
    boolean_sort);
}

// If `t` is a CONST_ARRAY term, return its sole child (the init value);
// otherwise return 0 (invalid term sentinel). Used by mk_eq / mk_neq to
// rewrite `(= ca(v1) ca(v2))` → `(= v1 v2)` so bitwuzla's incomplete
// "Equality over constant arrays" reasoning never has to fire.
static BitwuzlaTerm bitw_const_array_init_or_zero(BitwuzlaTerm t)
{
  if (bitwuzla_term_get_kind(t) != BITWUZLA_KIND_CONST_ARRAY)
    return 0;
  size_t n;
  BitwuzlaTerm *kids = bitwuzla_term_get_children(t, &n);
  if (n != 1)
    return 0;
  return kids[0];
}

smt_astt bitwuzla_convt::mk_eq(smt_astt a, smt_astt b)
{
  assert(a->sort->get_data_width() == b->sort->get_data_width());
  BitwuzlaTerm ta = to_solver_smt_ast<bitw_smt_ast>(a)->a;
  BitwuzlaTerm tb = to_solver_smt_ast<bitw_smt_ast>(b)->a;
  if (BitwuzlaTerm va = bitw_const_array_init_or_zero(ta))
    if (BitwuzlaTerm vb = bitw_const_array_init_or_zero(tb))
      return new_ast(
        bitwuzla_mk_term2(
          bitw_term_manager, BITWUZLA_KIND_EQUAL, va, vb),
        boolean_sort);
  return new_ast(
    bitwuzla_mk_term2(
      bitw_term_manager, BITWUZLA_KIND_EQUAL, ta, tb),
    boolean_sort);
}

smt_astt bitwuzla_convt::mk_neq(smt_astt a, smt_astt b)
{
  assert(a->sort->get_data_width() == b->sort->get_data_width());
  BitwuzlaTerm ta = to_solver_smt_ast<bitw_smt_ast>(a)->a;
  BitwuzlaTerm tb = to_solver_smt_ast<bitw_smt_ast>(b)->a;
  if (BitwuzlaTerm va = bitw_const_array_init_or_zero(ta))
    if (BitwuzlaTerm vb = bitw_const_array_init_or_zero(tb))
      return new_ast(
        bitwuzla_mk_term2(
          bitw_term_manager, BITWUZLA_KIND_DISTINCT, va, vb),
        boolean_sort);
  return new_ast(
    bitwuzla_mk_term2(
      bitw_term_manager, BITWUZLA_KIND_DISTINCT, ta, tb),
    boolean_sort);
}

smt_astt bitwuzla_convt::mk_store(smt_astt a, smt_astt b, smt_astt c)
{
  assert(a->sort->id == SMT_SORT_ARRAY);
  assert(a->sort->get_domain_width() == b->sort->get_data_width());
  assert(
    a->sort->get_range_sort()->get_data_width() == c->sort->get_data_width());
  return new_ast(
    bitwuzla_mk_term3(
      bitw_term_manager,
      BITWUZLA_KIND_ARRAY_STORE,
      to_solver_smt_ast<bitw_smt_ast>(a)->a,
      to_solver_smt_ast<bitw_smt_ast>(b)->a,
      to_solver_smt_ast<bitw_smt_ast>(c)->a),
    a->sort);
}

// ---- select pushed through array-sorted ITE -------------------------------
//
// Bitwuzla's array solver registers SELECT, STORE and EQUAL nodes only. An
// array-sorted ITE -- every SSA phi node over a mapping or a storage snapshot
// -- is therefore eliminated by its preprocessor into a fresh array constant
// plus two guarded equalities. When the branch being equated bottoms out in a
// const array (a fresh mapping is `(as const 0)`, and every store over it
// still does) the array solver answers "Equality over constant arrays not
// fully supported yet" and the query comes back `unknown`; a whole
// path-coverage unit then dies with `SMT solver failed` (GAZ_ERC20.transfer,
// ClockBoxContract.transferFrom: 22 claims, 1 reached the solver).
//
// So no array ITE is ever asserted: a select over `ite(c, A, B)` becomes
// `ite(c, select(A, i), select(B, i))`, and a select over a store that sits
// on top of such an ITE is read out as `ite(i = j, v, select(base, i))` until
// the ITE is reached. Arrays without an ITE underneath keep the native select.
// Memoised per (array, index) so a long store chain is expanded once.
static bool bitw_array_term_has_ite(
  BitwuzlaTerm t, std::unordered_map<BitwuzlaTerm, bool> &memo)
{
  auto it = memo.find(t);
  if (it != memo.end())
    return it->second;
  bool r = false;
  BitwuzlaKind k = bitwuzla_term_get_kind(t);
  if (k == BITWUZLA_KIND_ITE)
  {
    // Push only when a branch leads to a nested const array. Pushing every
    // array ITE was measured to turn a 10 s bytes32-push regression test
    // into 66 s once combined with the SSA substitution, and gains nothing
    // for flat arrays, which Bitwuzla handles natively.
    size_t n = 0;
    BitwuzlaTerm *p = bitwuzla_term_get_children(t, &n);
    BitwuzlaTerm c1 = n == 3 ? p[1] : 0, c2 = n == 3 ? p[2] : 0;
    r = n == 3 &&
        (bitw_array_term_has_ite(c1, memo) || bitw_array_term_has_ite(c2, memo));
  }
  else if (k == BITWUZLA_KIND_CONST_ARRAY)
    // A const array whose element is itself an array (a nested mapping,
    // `(as const (as const 0))`) is the other shape the array solver reports
    // as "Equality over constant arrays not fully supported": reading through
    // it is `select(const v, i) = v`, which the walker below applies directly.
    r = bitwuzla_sort_is_array(
      bitwuzla_sort_array_get_element(bitwuzla_term_get_sort(t)));
  else if (k == BITWUZLA_KIND_ARRAY_STORE)
  {
    size_t n = 0;
    BitwuzlaTerm *ch = bitwuzla_term_get_children(t, &n);
    r = n == 3 && bitw_array_term_has_ite(ch[0], memo);
  }
  memo[t] = r;
  return r;
}

static BitwuzlaTerm bitw_select_through_ite(
  BitwuzlaTermManager *tm,
  BitwuzlaTerm arr,
  BitwuzlaTerm idx,
  std::unordered_map<BitwuzlaTerm, bool> &has_ite,
  std::map<std::pair<BitwuzlaTerm, BitwuzlaTerm>, BitwuzlaTerm> &memo)
{
  auto key = std::make_pair(arr, idx);
  auto it = memo.find(key);
  if (it != memo.end())
    return it->second;
  BitwuzlaTerm r;
  BitwuzlaKind k = bitwuzla_term_get_kind(arr);
  // bitwuzla_term_get_children hands back a buffer the next call reuses, so
  // the children are copied out before any recursive call is made.
  size_t n = 0;
  BitwuzlaTerm ch[3] = {0, 0, 0};
  if (
    k == BITWUZLA_KIND_ITE || k == BITWUZLA_KIND_ARRAY_STORE ||
    k == BITWUZLA_KIND_CONST_ARRAY)
  {
    BitwuzlaTerm *p = bitwuzla_term_get_children(arr, &n);
    for (size_t i = 0; i < n && i < 3; i++)
      ch[i] = p[i];
  }
  if (k == BITWUZLA_KIND_CONST_ARRAY && n == 1 && bitw_array_term_has_ite(arr, has_ite))
    r = ch[0];
  else if (k == BITWUZLA_KIND_ITE && n == 3)
    r = bitwuzla_mk_term3(
      tm,
      BITWUZLA_KIND_ITE,
      ch[0],
      bitw_select_through_ite(tm, ch[1], idx, has_ite, memo),
      bitw_select_through_ite(tm, ch[2], idx, has_ite, memo));
  else if (
    k == BITWUZLA_KIND_ARRAY_STORE && n == 3 &&
    bitw_array_term_has_ite(ch[0], has_ite))
    r = bitwuzla_mk_term3(
      tm,
      BITWUZLA_KIND_ITE,
      bitwuzla_mk_term2(tm, BITWUZLA_KIND_EQUAL, idx, ch[1]),
      ch[2],
      bitw_select_through_ite(tm, ch[0], idx, has_ite, memo));
  else
    r = bitwuzla_mk_term2(tm, BITWUZLA_KIND_ARRAY_SELECT, arr, idx);
  memo[key] = r;
  return r;
}

smt_astt bitwuzla_convt::mk_select(smt_astt a, smt_astt b)
{
  assert(a->sort->id == SMT_SORT_ARRAY);
  assert(a->sort->get_domain_width() == b->sort->get_data_width());
  // The memos live on the converter: term ids are recycled when a term
  // manager is destroyed, so a process-wide cache would answer for a term
  // that no longer exists (measured on ClockBoxContract.transferFrom: a
  // stale `false` let a nested const array through to the solver again).
  auto &has_ite = sel_has_ite_memo;
  auto &memo = sel_memo;
  BitwuzlaTerm arr = to_solver_smt_ast<bitw_smt_ast>(a)->a;
  BitwuzlaTerm idx = to_solver_smt_ast<bitw_smt_ast>(b)->a;
  // ESBMC_NO_SELECT_PUSH=1 keeps the native select (diagnostic).
  static const bool no_push = getenv("ESBMC_NO_SELECT_PUSH") != nullptr;
  return new_ast(
    !no_push && bitw_array_term_has_ite(arr, has_ite)
      ? bitw_select_through_ite(bitw_term_manager, arr, idx, has_ite, memo)
      : bitwuzla_mk_term2(
          bitw_term_manager, BITWUZLA_KIND_ARRAY_SELECT, arr, idx),
    a->sort->get_range_sort());
}

smt_astt bitwuzla_convt::mk_smt_int(const BigInt &theint [[maybe_unused]])
{
  log_error("ESBMC can't create integer sorts with Bitwuzla yet");
  abort();
}

smt_astt bitwuzla_convt::mk_smt_real(const std::string &str [[maybe_unused]])
{
  log_error("ESBMC can't create real sorts with Bitwuzla yet");
  abort();
}

smt_astt bitwuzla_convt::mk_smt_bv(const BigInt &theint, smt_sortt s)
{
  return new_ast(
    bitwuzla_mk_bv_value(
      bitw_term_manager,
      to_solver_smt_sort<BitwuzlaSort>(s)->s,
      integer2binary(theint, s->get_data_width()).c_str(),
      2),
    s);
}

smt_astt bitwuzla_convt::mk_smt_bool(bool val)
{
  BitwuzlaTerm node = (val) ? bitwuzla_mk_true(bitw_term_manager)
                            : bitwuzla_mk_false(bitw_term_manager);
  const smt_sort *sort = boolean_sort;
  return new_ast(node, sort);
}

smt_astt bitwuzla_convt::mk_array_symbol(
  const std::string &name,
  const smt_sort *s,
  smt_sortt array_subtype [[maybe_unused]])
{
  return mk_smt_symbol(name, s);
}

smt_astt
bitwuzla_convt::mk_smt_symbol(const std::string &name, const smt_sort *s)
{
  symtabt::iterator it = symtable.find(name);
  if (it != symtable.end())
    return it->ast;

  BitwuzlaTerm node;

  switch (s->id)
  {
  case SMT_SORT_BV:
  case SMT_SORT_FIXEDBV:
  case SMT_SORT_BVFP:
  case SMT_SORT_BVFP_RM:
  case SMT_SORT_BOOL:
  case SMT_SORT_ARRAY:
    node = bitwuzla_mk_const(
      bitw_term_manager, to_solver_smt_sort<BitwuzlaSort>(s)->s, name.c_str());
    break;

  default:
    log_error("Unknown type for symbol");
    abort();
  }

  smt_astt ast = new_ast(node, s);

  symtable.emplace(name, ast, ctx_level);

  return ast;
}

smt_astt
bitwuzla_convt::mk_extract(smt_astt a, unsigned int high, unsigned int low)
{
  smt_sortt s = mk_bv_sort(high - low + 1);
  const bitw_smt_ast *ast = to_solver_smt_ast<bitw_smt_ast>(a);
  BitwuzlaTerm b = bitwuzla_mk_term1_indexed2(
    bitw_term_manager, BITWUZLA_KIND_BV_EXTRACT, ast->a, high, low);
  return new_ast(b, s);
}

smt_astt bitwuzla_convt::mk_sign_ext(smt_astt a, unsigned int topwidth)
{
  smt_sortt s = mk_bv_sort(a->sort->get_data_width() + topwidth);
  const bitw_smt_ast *ast = to_solver_smt_ast<bitw_smt_ast>(a);
  BitwuzlaTerm b = bitwuzla_mk_term1_indexed1(
    bitw_term_manager, BITWUZLA_KIND_BV_SIGN_EXTEND, ast->a, topwidth);
  return new_ast(b, s);
}

smt_astt bitwuzla_convt::mk_zero_ext(smt_astt a, unsigned int topwidth)
{
  smt_sortt s = mk_bv_sort(a->sort->get_data_width() + topwidth);
  const bitw_smt_ast *ast = to_solver_smt_ast<bitw_smt_ast>(a);
  BitwuzlaTerm b = bitwuzla_mk_term1_indexed1(
    bitw_term_manager, BITWUZLA_KIND_BV_ZERO_EXTEND, ast->a, topwidth);
  return new_ast(b, s);
}

smt_astt bitwuzla_convt::mk_concat(smt_astt a, smt_astt b)
{
  smt_sortt s =
    mk_bv_sort(a->sort->get_data_width() + b->sort->get_data_width());

  return new_ast(
    bitwuzla_mk_term2(
      bitw_term_manager,
      BITWUZLA_KIND_BV_CONCAT,
      to_solver_smt_ast<bitw_smt_ast>(a)->a,
      to_solver_smt_ast<bitw_smt_ast>(b)->a),
    s);
}

smt_astt bitwuzla_convt::mk_ite(smt_astt cond, smt_astt t, smt_astt f)
{
  assert(cond->sort->id == SMT_SORT_BOOL);
  assert(t->sort->get_data_width() == f->sort->get_data_width());

  return new_ast(
    bitwuzla_mk_term3(
      bitw_term_manager,
      BITWUZLA_KIND_ITE,
      to_solver_smt_ast<bitw_smt_ast>(cond)->a,
      to_solver_smt_ast<bitw_smt_ast>(t)->a,
      to_solver_smt_ast<bitw_smt_ast>(f)->a),
    t->sort);
}

bool bitwuzla_convt::get_bool(smt_astt a)
{
  const bitw_smt_ast *ast = to_solver_smt_ast<bitw_smt_ast>(a);

  const char *result =
    bitwuzla_term_to_string(bitwuzla_get_value(bitw, ast->a));

  assert(result != NULL && "Bitwuzla returned null bv value string");

  bool res;
  if (!strcmp(result, "true"))
    res = true;
  else if (!strcmp(result, "false"))
    res = false;
  else
  {
    log_error("Can't get boolean value from Bitwuzla: {}", result);
    abort();
  }

  return res;
}

BigInt bitwuzla_convt::get_bv(smt_astt a, bool is_signed)
{
  const bitw_smt_ast *ast = to_solver_smt_ast<bitw_smt_ast>(a);
  const char *result =
    bitwuzla_term_value_get_str(bitwuzla_get_value(bitw, ast->a));
  BigInt val = binary2integer(result, is_signed);
  return val;
}

expr2tc bitwuzla_convt::get_array_elem(
  smt_astt array,
  uint64_t index,
  const type2tc &subtype)
{
  const bitw_smt_ast *za = to_solver_smt_ast<bitw_smt_ast>(array);
  size_t array_bound = array->sort->get_domain_width();
  const bitw_smt_ast *idx;
  if (int_encoding)
    idx = to_solver_smt_ast<bitw_smt_ast>(mk_smt_int(BigInt(index)));
  else
    idx = to_solver_smt_ast<bitw_smt_ast>(
      mk_smt_bv(BigInt(index), mk_bv_sort(array_bound)));

  BitwuzlaTerm e = bitwuzla_mk_term2(
    bitw_term_manager,
    BITWUZLA_KIND_ARRAY_SELECT,
    bitwuzla_get_value(bitw, za->a),
    bitwuzla_get_value(bitw, idx->a));

  return get_by_ast(subtype, new_ast(e, convert_sort(subtype)));
}

smt_astt bitwuzla_convt::overflow_arith(const expr2tc &expr)
{
  const overflow2t &overflow = to_overflow2t(expr);
  const arith_2ops &opers = static_cast<const arith_2ops &>(*overflow.operand);

  const bitw_smt_ast *side1 =
    to_solver_smt_ast<bitw_smt_ast>(convert_ast(opers.side_1));
  const bitw_smt_ast *side2 =
    to_solver_smt_ast<bitw_smt_ast>(convert_ast(opers.side_2));

  // Guess whether we're performing a signed or unsigned comparison.
  bool is_signed =
    (is_signedbv_type(opers.side_1) || is_signedbv_type(opers.side_2));

  BitwuzlaTerm res;
  if (is_add2t(overflow.operand))
  {
    if (is_signed)
    {
      res = bitwuzla_mk_term2(
        bitw_term_manager, BITWUZLA_KIND_BV_SADD_OVERFLOW, side1->a, side2->a);
    }
    else
    {
      res = bitwuzla_mk_term2(
        bitw_term_manager, BITWUZLA_KIND_BV_UADD_OVERFLOW, side1->a, side2->a);
    }
  }
  else if (is_sub2t(overflow.operand))
  {
    if (is_signed)
    {
      res = bitwuzla_mk_term2(
        bitw_term_manager, BITWUZLA_KIND_BV_SSUB_OVERFLOW, side1->a, side2->a);
    }
    else
    {
      res = bitwuzla_mk_term2(
        bitw_term_manager, BITWUZLA_KIND_BV_USUB_OVERFLOW, side1->a, side2->a);
    }
  }
  else if (is_mul2t(overflow.operand))
  {
    if (is_signed)
    {
      res = bitwuzla_mk_term2(
        bitw_term_manager, BITWUZLA_KIND_BV_SMUL_OVERFLOW, side1->a, side2->a);
    }
    else
    {
      res = bitwuzla_mk_term2(
        bitw_term_manager, BITWUZLA_KIND_BV_UMUL_OVERFLOW, side1->a, side2->a);
    }
  }
  else if (is_div2t(overflow.operand) || is_modulus2t(overflow.operand))
  {
    res = bitwuzla_mk_term2(
      bitw_term_manager, BITWUZLA_KIND_BV_SDIV_OVERFLOW, side1->a, side2->a);
  }
  else
  {
    return smt_convt::overflow_arith(expr);
  }

  const smt_sort *s = boolean_sort;
  return new_ast(res, s);
}

smt_astt
bitwuzla_convt::convert_array_of(smt_astt init_val, unsigned long domain_width)
{
  smt_sortt dom_sort = mk_int_bv_sort(domain_width);
  smt_sortt arrsort = mk_array_sort(dom_sort, init_val->sort);

  return new_ast(
    bitwuzla_mk_const_array(
      bitw_term_manager,
      to_solver_smt_sort<BitwuzlaSort>(arrsort)->s,
      to_solver_smt_ast<bitw_smt_ast>(init_val)->a),
    arrsort);
}

std::string bitwuzla_convt::dump_smt()
{
  FILE *temp_file = tmpfile();
  if (!temp_file)
  {
    log_error("Failed to create temporary file for SMT dump");
    return "";
  }

  bitwuzla_print_formula(bitw, "smt2", temp_file, 2);

  // Get file size and read entire content
  fseek(temp_file, 0, SEEK_END);
  long file_size = ftell(temp_file);
  fseek(temp_file, 0, SEEK_SET);

  if (file_size <= 0)
  {
    fclose(temp_file);
    return "";
  }

  // Allocate buffer for entire file content
  std::vector<char> buffer(file_size + 1);
  size_t bytes_read = fread(buffer.data(), 1, file_size, temp_file);
  buffer[bytes_read] = '\0'; // Null terminate

  fclose(temp_file);
  return std::string(buffer.data(), bytes_read);
}

void bitw_smt_ast::dump() const
{
  log_status("{}", bitwuzla_term_to_string(a));
}

void bitwuzla_convt::print_model()
{
  log_warning(
    "Bitwuzla model printing is experimental and does not guarantee correct "
    "results.");
  // TODO: We use `symbtable` to get all symbols, because there does not seem to
  // be a way to get all symbols from Bitwuzla. This is not ideal, because
  // I have no idea whether ignoring the `level` field, which is also part of
  // an entry in `symbtable`, is correct. However, it seems to work for now.
  for (const auto &entry : symtable)
  {
    smt_astt term = entry.ast;
    BitwuzlaSort sort =
      bitwuzla_term_get_sort(to_solver_smt_ast<bitw_smt_ast>(term)->a);
    fprintf(
      messaget::state.out,
      "(define-fun %s (",
      bitwuzla_term_get_symbol(to_solver_smt_ast<bitw_smt_ast>(term)->a));
    if (bitwuzla_sort_is_fun(sort))
    {
      BitwuzlaTerm value =
        bitwuzla_get_value(bitw, to_solver_smt_ast<bitw_smt_ast>(term)->a);
      size_t size;
      BitwuzlaTerm *children = bitwuzla_term_get_children(value, &size);
      assert(size == 2);
      while (bitwuzla_term_get_kind(children[1]) == BITWUZLA_KIND_LAMBDA)
      {
        assert(bitwuzla_term_is_var(children[0]));
        fprintf(
          messaget::state.out,
          "(%s %s) ",
          bitwuzla_term_to_string(children[0]),
          bitwuzla_sort_to_string(bitwuzla_term_get_sort(children[0])));
        value = children[1];
        children = bitwuzla_term_get_children(value, &size);
      }
      assert(bitwuzla_term_is_var(children[0]));
      // Note: The returned string of bitwuzla_term_to_string and
      //       bitwuzla_sort_to_string does not have to be freed, but is only
      //       valid until the next call to the respective function. Thus we
      //       split printing into separate printf calls so that none of these
      //       functions is called more than once in one printf call.
      //       Alternatively, we could also first get and copy the strings, use
      //       a single printf call, and then free the copied strings.
      fprintf(
        messaget::state.out,
        "(%s %s))",
        bitwuzla_term_to_string(children[0]),
        bitwuzla_sort_to_string(bitwuzla_term_get_sort(children[0])));
      fprintf(
        messaget::state.out,
        " %s",
        bitwuzla_sort_to_string(bitwuzla_sort_fun_get_codomain(sort)));
      fprintf(
        messaget::state.out, " %s)\n", bitwuzla_term_to_string(children[1]));
    }
    else
    {
      fprintf(
        messaget::state.out,
        ") %s %s)\n",
        bitwuzla_sort_to_string(sort),
        bitwuzla_term_to_string(
          bitwuzla_get_value(bitw, to_solver_smt_ast<bitw_smt_ast>(term)->a)));
    }
  }
}

smt_sortt bitwuzla_convt::mk_bool_sort()
{
  return new solver_smt_sort<BitwuzlaSort>(
    SMT_SORT_BOOL, bitwuzla_mk_bool_sort(bitw_term_manager), 1);
}

smt_sortt bitwuzla_convt::mk_bv_sort(std::size_t width)
{
  return new solver_smt_sort<BitwuzlaSort>(
    SMT_SORT_BV, bitwuzla_mk_bv_sort(bitw_term_manager, width), width);
}

smt_sortt bitwuzla_convt::mk_fbv_sort(std::size_t width)
{
  return new solver_smt_sort<BitwuzlaSort>(
    SMT_SORT_FIXEDBV, bitwuzla_mk_bv_sort(bitw_term_manager, width), width);
}

smt_sortt bitwuzla_convt::mk_array_sort(smt_sortt domain, smt_sortt range)
{
  auto domain_sort = to_solver_smt_sort<BitwuzlaSort>(domain);
  auto range_sort = to_solver_smt_sort<BitwuzlaSort>(range);

  auto t =
    bitwuzla_mk_array_sort(bitw_term_manager, domain_sort->s, range_sort->s);
  return new solver_smt_sort<BitwuzlaSort>(
    SMT_SORT_ARRAY, t, domain_sort->get_data_width(), range);
}

smt_sortt bitwuzla_convt::mk_bvfp_sort(std::size_t ew, std::size_t sw)
{
  return new solver_smt_sort<BitwuzlaSort>(
    SMT_SORT_BVFP,
    bitwuzla_mk_bv_sort(bitw_term_manager, ew + sw + 1),
    ew + sw + 1,
    sw + 1);
}

smt_sortt bitwuzla_convt::mk_bvfp_rm_sort()
{
  return new solver_smt_sort<BitwuzlaSort>(
    SMT_SORT_BVFP_RM, bitwuzla_mk_bv_sort(bitw_term_manager, 3), 3);
}

smt_astt bitwuzla_convt::mk_quantifier(
  bool is_forall,
  std::vector<smt_astt> lhs,
  smt_astt rhs)
{
  std::vector<BitwuzlaTerm> original_terms;
  std::vector<BitwuzlaTerm> bound_vars;
  original_terms.reserve(lhs.size());
  bound_vars.reserve(lhs.size());

  for (size_t i = 0; i < lhs.size(); i++)
  {
    BitwuzlaTerm orig = to_solver_smt_ast<bitw_smt_ast>(lhs[i])->a;
    original_terms.push_back(orig);
    std::string name =
      "qvar_" + std::to_string(quantifier_counter) + "_" + std::to_string(i);
    bound_vars.push_back(bitwuzla_mk_var(
      bitw_term_manager, bitwuzla_term_get_sort(orig), name.c_str()));
  }

  // Substitute SSA terms with bound vars in the body.
  // Args to bitwuzla_mk_term: [var0, ..., varN-1, body] — no VARIABLE_LIST
  // wrapper needed (unlike CVC5).
  BitwuzlaTerm body = bitwuzla_substitute_term(
    to_solver_smt_ast<bitw_smt_ast>(rhs)->a,
    original_terms.size(),
    original_terms.data(),
    bound_vars.data());

  std::vector<BitwuzlaTerm> args(bound_vars);
  args.push_back(body);

  return new_ast(
    bitwuzla_mk_term(
      bitw_term_manager,
      is_forall ? BITWUZLA_KIND_FORALL : BITWUZLA_KIND_EXISTS,
      static_cast<uint32_t>(args.size()),
      args.data()),
    rhs->sort);
}
