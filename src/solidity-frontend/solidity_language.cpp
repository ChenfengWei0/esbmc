#include <util/compiler_defs.h>
// Remove warnings from Clang headers
CC_DIAGNOSTIC_PUSH()
CC_DIAGNOSTIC_IGNORE_LLVM_CHECKS()
#include <clang/Frontend/ASTUnit.h>
CC_DIAGNOSTIC_POP()

#include <solidity-frontend/solidity_language.h>
#include <solidity-frontend/solidity_convert.h>
#include <clang-cpp-frontend/clang_cpp_main.h>
#include <clang-cpp-frontend/clang_cpp_adjust.h>
#include <clang-cpp-frontend/clang_cpp_convert.h>
#include <c2goto/cprover_library.h>
#include <util/c_link.h>
#include "filesystem.h"
#include <unordered_set>
#include <unordered_map>

languaget *new_solidity_language()
{
  return new solidity_languaget;
}

solidity_languaget::solidity_languaget()
{
  std::string fun = config.options.get_option("function");
  if (!fun.empty())
    func_name = fun;
  else
    func_name = "";

  std::string cnt = config.options.get_option("contract");
  if (!cnt.empty())
    contract_names = cnt;
  else
    contract_names = "";

  std::string sol = config.options.get_option("sol");
  if (sol.empty())
  {
    log_error("Please set the smart contract source file via --sol");
    abort();
  }
  contract_path = sol;
}

std::string solidity_languaget::get_temp_file()
{
  // Create a minimal temp file for clang-tool to parse ESBMC intrinsic symbols.
  // Only includes standard headers (for nondet, assert, etc.) and a dummy main.
  // Solidity operational models are loaded separately from c2goto (sol64).
  static std::once_flag flag;
  static std::string p;

  std::call_once(flag, [&]() {
    p = file_operations::create_tmp_dir("esbmc_solidity_temp-%%%%-%%%%-%%%%")
          .path();
    boost::filesystem::create_directories(p);
    p += "/intrinsics.cpp";
    std::ofstream f(p);
    if (!f)
    {
      log_error(
        "Can't create temporary directory (needed to convert intrinsics)");
      abort();
    }
    f << R"(
#include <stddef.h>
#include <stdlib.h>
#include <stdint.h>
#include <stdbool.h>
#include <assert.h>
#include <string.h>
#include <ctype.h>
int main() { return 0; }
)";
  });

  return p;
}

bool solidity_languaget::parse(const std::string &path)
{
  // Phase 1: Parse a minimal C++ file through Clang to get ESBMC intrinsic
  // symbols (nondet_bool, nondet_uint, __ESBMC_assert, etc.)
  temp_path = get_temp_file();
  auto sol_lang = std::exchange(config.language, {language_idt::CPP, ""});
  if (clang_cpp_languaget::parse(temp_path))
    return true;
  config.language = std::move(sol_lang);

  // Phase 2: Parse Solidity AST JSON
  std::ifstream ast_json_file_stream(path);
  std::string new_line;
  std::vector<nlohmann::json> json_blocks;
  std::string current_json_block;

  // Skip the initial part until the first ".sol ======="
  while (getline(ast_json_file_stream, new_line))
  {
    if (new_line.find(".sol =======") != std::string::npos)
    {
      break;
    }
  }
  // Read and parse each JSON block separately
  while (getline(ast_json_file_stream, new_line))
  {
    if (new_line.find(".sol =======") != std::string::npos)
    {
      if (!current_json_block.empty())
      {
        json_blocks.push_back(nlohmann::json::parse(current_json_block));
        current_json_block.clear();
      }
    }
    else
    {
      current_json_block += new_line + "\n";
    }
  }

  // Parse the last JSON block
  if (!current_json_block.empty())
  {
    json_blocks.push_back(nlohmann::json::parse(current_json_block));
  }

  // Combine all parsed JSON blocks into one JSON array
  for (const auto &block : json_blocks)
  {
    src_ast_json_array.push_back(block);
  }
  return false;
}

bool solidity_languaget::convert_intrinsics(contextt &context)
{
  clang_cpp_convertert converter(context, AST, "C++");
  if (converter.convert())
    return true;

  return false;
}

bool solidity_languaget::typecheck(contextt &context, const std::string &module)
{
  contextt new_context;

  // Phase 1: Convert ESBMC intrinsic symbols (nondet_bool, nondet_uint,
  // __ESBMC_assert, etc.) from Clang AST into the context.
  convert_intrinsics(new_context);

  // Phase 2: Load Solidity operational models from the separate sol64 goto
  // binary (builtins, mapping, array, bytes, string, address, units, misc).
  add_cprover_library(new_context, this);

  // Record which symbols came from phases 1+2 (before converter adds its own)
  std::unordered_set<std::string> lib_symbols;
  new_context.foreach_operand(
    [&lib_symbols](const symbolt &s) { lib_symbols.insert(s.id.as_string()); });

  // Phase 3: Convert Solidity AST to ESBMC IR
  solidity_convertert converter(
    new_context, src_ast_json_array, contract_names, func_name, contract_path);
  if (converter.convert())
    return true;

  // Phase 4: Adjust converter-generated code. Save and restore sol64 function
  // bodies because clang_cpp_adjust would corrupt them (they are already
  // adjusted by c2goto's clang_c_adjust).
  std::unordered_map<std::string, exprt> saved_values;
  new_context.Foreach_operand([&](symbolt &s) {
    if (lib_symbols.count(s.id.as_string()) && s.value.is_not_nil())
      saved_values[s.id.as_string()] = s.value;
  });

  clang_cpp_adjust adjuster(new_context);
  if (adjuster.adjust())
    return true;

  // Restore pre-adjusted function bodies from intrinsics and sol64
  for (auto &[id, val] : saved_values)
  {
    symbolt *s = new_context.find_symbol(id);
    if (s)
      s->value = std::move(val);
  }

  if (c_link(
        context, new_context, module)) // also populates language_uit::context
    return true;

  return false;
}

void solidity_languaget::show_parse(std::ostream &)
{
  assert(!"come back and continue - solidity_languaget::show_parse");
}

bool solidity_languaget::final(contextt &context)
{
  add_cprover_library(context);
  clang_cpp_maint c_main(context);
  if (c_main.clang_main())
    return true;

  // roll back
  config.language = {language_idt::SOLIDITY, ""};
  return false;
}
