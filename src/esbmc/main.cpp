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
#include <langapi/mode.h>
#include <util/message/default_message.h>
#include <irep2/irep2.h>
#include <goto-programs/goto_mutation.h>

esbmc_parseoptionst *ptr = NULL;


bool VulnerableFunction1(const uint8_t *data, size_t size)
{
  bool result = false;
  if(size >= 3)
  {
    result =
      data[0] == 'F' && data[1] == 'U' && data[2] == 'Z' && data[3] == 'Z';
  }

  return result;
}

// extern "C" int LLVMFuzzerRunDriver(
//   int *argc,
//   char ***argv,
//   int (*UserCb)(const uint8_t *Data, size_t Size));

// extern "C" int LLVMFuzzerTestOneInput(const uint8_t *Data, size_t Size)
// {
//   uint8_t *t = (uint8_t *)Data;
//   goto_mutationt tmp(t, Size);
//   tmp.mutateSequence((*ptr).goto_functions, msg);
//   // VulnerableFunction1(Data, Size);
//   //printf("1111111\n");
//   return 0;
// }

int main(int argc, const char **argv)
{
  //char **aargv = (char **)argv;
  messaget msg;
  esbmc_parseoptionst parseoptions(argc, argv, msg);
  parseoptions.main();
  //ptr = &parseoptions;
  //LLVMFuzzerRunDriver(&argc, &aargv, LLVMFuzzerTestOneInput);
}
