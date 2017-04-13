/*******************************************************************\

Module: ANSI-C Conversion / Type Checking

Author: Daniel Kroening, kroening@kroening.com

\*******************************************************************/

#ifndef CPROVER_ANSI_C_PARSE_FLOAT_H
#define CPROVER_ANSI_C_PARSE_FLOAT_H

#include <util/mp_arith.h>
#include <string>

void parse_float(
  const std::string &src,
  mp_integer &significand,
  mp_integer &exponent,
  unsigned &exponent_base,
  bool &is_float,
  bool &is_long);

#endif
