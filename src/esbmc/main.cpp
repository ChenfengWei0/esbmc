/*******************************************************************\

Module: Main Module

Author: Lucas Cordeiro, lcc08r@ecs.soton.ac.uk
		Jeremy Morse, jcmm106@ecs.soton.ac.uk

\*******************************************************************/

/*

  ESBMC
  SMT-based Context-Bounded Model Checking for ANSI-C/C++
  Copyright (c) 2009-2011, Lucas Cordeiro, Federal University of Amazonas
  Jeremy Morse, Denis Nicole, Bernd Fischer, University of Southampton,
  Joao Marques Silva, University College Dublin.
  All rights reserved.

*/

#include <cstdint>
#include <stdint.h>
#include <stdio.h>
#include <esbmc/esbmc_parseoptions.h>
#include <esbmc/goto_fuzz.h>
#include <langapi/mode.h>
#include <util/message/default_message.h>
#include <irep2/irep2.h>
#include <cstdlib>

// static esbmc_parseoptionst *ptr = NULL;
// static goto_functionst func;

int main(int argc, const char **argv)
{
  messaget msg;
  esbmc_parseoptionst parseoptions(argc, argv, msg);
  parseoptions.main();

  if(parseoptions.enable_goto_fuzz)
  {
    goto_fuzz fuzzer;
    fuzzer.fuzz_timeout = parseoptions.gf_maxtime;
    fuzzer.fuzz_init(&parseoptions);
    LLVMFuzzerRunDriver(&fuzzer.fargc, &fuzzer.fargv, LLVMFuzzerTestOneInput);
  }
}
