/* Solidity string operations and conversions */
#include <stddef.h>
#include <stdlib.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include "solidity_types.h"

char* string_concat(char *x, char *y)
{
__ESBMC_HIDE:;
	strncat(x, y, 256);
	return x;
}

char get_char(int digit)
{
__ESBMC_HIDE:;
    char charstr[] = "0123456789ABCDEF";
    return charstr[digit];
}

void sol_rev(char *p)
{
__ESBMC_HIDE:;
	char *q = &p[strlen(p) - 1];
	char *r = p;
	for (; q > r; q--, r++)
	{
		char s = *q;
		*q = *r;
		*r = s;
	}
}

char *i256toa(int256_t value)
{
__ESBMC_HIDE:;
	// we might have memory leak as we will not free this afterwards
	char *str = (char *)malloc(256 * sizeof(char));
	int256_t base = (int256_t)10;
	unsigned short count = 0;
	bool flag = true;

	if (value < (int256_t)0 && base == (int256_t)10)
	{
		flag = false;
	}
	if (value == (int256_t)0)
	{
		str[count] = '\0';
		return str;
	}
	while (value != (int256_t)0)
	{
		int256_t dig = value % base;
		value -= dig;
		value /= base;

		if (flag == true)
			str[count] = get_char(dig);
		else
			str[count] = get_char(-dig);
		count++;
	}
	if (flag == false)
	{
		str[count] = '-';
		count++;
	}
	str[count] = 0;
	sol_rev(str);
	return str;
}

char *u256toa(uint256_t value)
{
__ESBMC_HIDE:;
	char *str = (char *)malloc(256 * sizeof(char));
	uint256_t base = (uint256_t)10;
	unsigned short count = 0;
	if (value == (uint256_t)0)
	{
		str[count] = '\0';
		return str;
	}
	while (value != (uint256_t)0)
	{
		uint256_t dig = value % base;
		value -= dig;
		value /= base;
		str[count] = get_char(dig);
		count++;
	}
	str[count] = 0;
	sol_rev(str);
	return str;
}

char *decToHexa(int n)
{
__ESBMC_HIDE:;
    char *hexaDeciNum = (char *)malloc(256 * sizeof(char));
    hexaDeciNum[0] = '\0';
    int i = 0;
    while (n != 0)
    {
        int temp = 0;
        temp = n % 16;
        if (temp < 10)
        {
            hexaDeciNum[i] = temp + 48;
            i++;
        }
        else
        {
            hexaDeciNum[i] = temp + 55;
            i++;
        }

        n /= 16;
    }
    char *ans = (char *)malloc(256 * sizeof(char));
    ans[0] = '\0';
    int pos = 0;
    for (int j = i - 1; j >= 0; j--)
    {
        ans[pos] = (char)hexaDeciNum[j];
        pos++;
    }
    ans[pos] = '\0';
    return ans;
}

char *ASCIItoHEX(const char *ascii)
{
__ESBMC_HIDE:;
    char *hex = (char *)malloc(256 * sizeof(char));
    hex[0] = '\0';
    for (int i = 0; i < strlen(ascii); i++)
    {
        char ch = ascii[i];
        int tmp = (int)ch;
        char *part = decToHexa(tmp);
        strcat(hex, part);
    }
    return hex;
}

uint256_t hexdec(const char *hex)
{
__ESBMC_HIDE:;
    /*https://stackoverflow.com/questions/10324/convert-a-hexadecimal-string-to-an-integer-efficiently-in-c*/

    static const long hextable[] = {
      -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1,
      -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1,
      -1, -1, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, -1, -1, -1, -1, -1, -1, -1, 10, 11, 12, 13, 14, 15, -1,
      -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1,
      -1, -1, 10, 11, 12, 13, 14, 15, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1,
      -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1,
      -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1,
      -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1,
      -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1,
      -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1,
      -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1};
    uint256_t ret = 0;
    while (*hex && ret >= (uint256_t)0)
    {
        ret = (ret << (uint256_t)4) | (uint256_t)hextable[*hex++];
    }
    return ret;
}

uint256_t str2uint(const char *str)
{
__ESBMC_HIDE:;
    return hexdec(ASCIItoHEX(str));
}

#define _ESBMC_SOL_STR_MAX 64

// string assign
void _str_assign(char **str1, const char *str2) {
__ESBMC_HIDE:;
    // Ensure str1 is a valid pointer (not NULL)
    if (str1 == NULL) {
        return;  // Early exit if str1 is invalid
    }
    // Free *str1 only if it was previously allocated (non-NULL)
    // if (*str1 != NULL) {
    //     free(*str1);
    // }

    // If str2 is NULL, set *str1 to NULL (avoid dangling pointers)
    if (str2 == NULL) {
        *str1 = NULL;
        return;
    }
    /* Keep the old 64-byte cap, but do not express it as loops. Solidity
     * constructors often assign token name/symbol strings before any harness
     * transaction. If this model depends on the global unwind bound, a short
     * default bound can truncate the constructor and make every later path
     * coverage claim vacuous. */
    *str1 = (char *)malloc(_ESBMC_SOL_STR_MAX + 1);
#define _ESBMC_STR_ASSIGN_STEP(IDX)                                            \
    (*str1)[IDX] = str2[IDX];                                                  \
    if (str2[IDX] == '\0')                                                     \
        return
    _ESBMC_STR_ASSIGN_STEP(0);
    _ESBMC_STR_ASSIGN_STEP(1);
    _ESBMC_STR_ASSIGN_STEP(2);
    _ESBMC_STR_ASSIGN_STEP(3);
    _ESBMC_STR_ASSIGN_STEP(4);
    _ESBMC_STR_ASSIGN_STEP(5);
    _ESBMC_STR_ASSIGN_STEP(6);
    _ESBMC_STR_ASSIGN_STEP(7);
    _ESBMC_STR_ASSIGN_STEP(8);
    _ESBMC_STR_ASSIGN_STEP(9);
    _ESBMC_STR_ASSIGN_STEP(10);
    _ESBMC_STR_ASSIGN_STEP(11);
    _ESBMC_STR_ASSIGN_STEP(12);
    _ESBMC_STR_ASSIGN_STEP(13);
    _ESBMC_STR_ASSIGN_STEP(14);
    _ESBMC_STR_ASSIGN_STEP(15);
    _ESBMC_STR_ASSIGN_STEP(16);
    _ESBMC_STR_ASSIGN_STEP(17);
    _ESBMC_STR_ASSIGN_STEP(18);
    _ESBMC_STR_ASSIGN_STEP(19);
    _ESBMC_STR_ASSIGN_STEP(20);
    _ESBMC_STR_ASSIGN_STEP(21);
    _ESBMC_STR_ASSIGN_STEP(22);
    _ESBMC_STR_ASSIGN_STEP(23);
    _ESBMC_STR_ASSIGN_STEP(24);
    _ESBMC_STR_ASSIGN_STEP(25);
    _ESBMC_STR_ASSIGN_STEP(26);
    _ESBMC_STR_ASSIGN_STEP(27);
    _ESBMC_STR_ASSIGN_STEP(28);
    _ESBMC_STR_ASSIGN_STEP(29);
    _ESBMC_STR_ASSIGN_STEP(30);
    _ESBMC_STR_ASSIGN_STEP(31);
    _ESBMC_STR_ASSIGN_STEP(32);
    _ESBMC_STR_ASSIGN_STEP(33);
    _ESBMC_STR_ASSIGN_STEP(34);
    _ESBMC_STR_ASSIGN_STEP(35);
    _ESBMC_STR_ASSIGN_STEP(36);
    _ESBMC_STR_ASSIGN_STEP(37);
    _ESBMC_STR_ASSIGN_STEP(38);
    _ESBMC_STR_ASSIGN_STEP(39);
    _ESBMC_STR_ASSIGN_STEP(40);
    _ESBMC_STR_ASSIGN_STEP(41);
    _ESBMC_STR_ASSIGN_STEP(42);
    _ESBMC_STR_ASSIGN_STEP(43);
    _ESBMC_STR_ASSIGN_STEP(44);
    _ESBMC_STR_ASSIGN_STEP(45);
    _ESBMC_STR_ASSIGN_STEP(46);
    _ESBMC_STR_ASSIGN_STEP(47);
    _ESBMC_STR_ASSIGN_STEP(48);
    _ESBMC_STR_ASSIGN_STEP(49);
    _ESBMC_STR_ASSIGN_STEP(50);
    _ESBMC_STR_ASSIGN_STEP(51);
    _ESBMC_STR_ASSIGN_STEP(52);
    _ESBMC_STR_ASSIGN_STEP(53);
    _ESBMC_STR_ASSIGN_STEP(54);
    _ESBMC_STR_ASSIGN_STEP(55);
    _ESBMC_STR_ASSIGN_STEP(56);
    _ESBMC_STR_ASSIGN_STEP(57);
    _ESBMC_STR_ASSIGN_STEP(58);
    _ESBMC_STR_ASSIGN_STEP(59);
    _ESBMC_STR_ASSIGN_STEP(60);
    _ESBMC_STR_ASSIGN_STEP(61);
    _ESBMC_STR_ASSIGN_STEP(62);
    _ESBMC_STR_ASSIGN_STEP(63);
#undef _ESBMC_STR_ASSIGN_STEP
    (*str1)[_ESBMC_SOL_STR_MAX] = '\0';
}

unsigned int nondet_uint();
char nondet_char();

/* Fixed-size buffer (not __ESBMC_inf_size) so a concrete NUL at index
 * _ESBMC_NONDET_STRING_MAX guarantees strlen() terminates without the
 * generic scan loop unwinding indefinitely. */
#define _ESBMC_NONDET_STRING_MAX 32
char _ESBMC_rand_str[_ESBMC_NONDET_STRING_MAX + 1];

/* Cap the symbolic length so symex terminates without --unwind. Without
 * a concrete loop bound, len is symbolic and the fill loop would unwind
 * forever; without a zero-initialised tail, strlen() on the returned
 * string would keep scanning symbolic bytes past the NUL until OOM. */
char *nondet_string() {
__ESBMC_HIDE:;
    size_t len = nondet_uint();
    __ESBMC_assume(len < _ESBMC_NONDET_STRING_MAX);

    /* No loop here: this helper runs before the target path claims, and a
     * truncated helper loop can make the harness look unreachable. */
#define _ESBMC_NONDET_STRING_STEP(i) \
    do { \
        if ((i) < len) { \
            _ESBMC_rand_str[(i)] = nondet_char(); \
            __ESBMC_assume(_ESBMC_rand_str[(i)] != '\0'); \
        } \
    } while (0)
    _ESBMC_NONDET_STRING_STEP(0);
    _ESBMC_NONDET_STRING_STEP(1);
    _ESBMC_NONDET_STRING_STEP(2);
    _ESBMC_NONDET_STRING_STEP(3);
    _ESBMC_NONDET_STRING_STEP(4);
    _ESBMC_NONDET_STRING_STEP(5);
    _ESBMC_NONDET_STRING_STEP(6);
    _ESBMC_NONDET_STRING_STEP(7);
    _ESBMC_NONDET_STRING_STEP(8);
    _ESBMC_NONDET_STRING_STEP(9);
    _ESBMC_NONDET_STRING_STEP(10);
    _ESBMC_NONDET_STRING_STEP(11);
    _ESBMC_NONDET_STRING_STEP(12);
    _ESBMC_NONDET_STRING_STEP(13);
    _ESBMC_NONDET_STRING_STEP(14);
    _ESBMC_NONDET_STRING_STEP(15);
    _ESBMC_NONDET_STRING_STEP(16);
    _ESBMC_NONDET_STRING_STEP(17);
    _ESBMC_NONDET_STRING_STEP(18);
    _ESBMC_NONDET_STRING_STEP(19);
    _ESBMC_NONDET_STRING_STEP(20);
    _ESBMC_NONDET_STRING_STEP(21);
    _ESBMC_NONDET_STRING_STEP(22);
    _ESBMC_NONDET_STRING_STEP(23);
    _ESBMC_NONDET_STRING_STEP(24);
    _ESBMC_NONDET_STRING_STEP(25);
    _ESBMC_NONDET_STRING_STEP(26);
    _ESBMC_NONDET_STRING_STEP(27);
    _ESBMC_NONDET_STRING_STEP(28);
    _ESBMC_NONDET_STRING_STEP(29);
    _ESBMC_NONDET_STRING_STEP(30);
    _ESBMC_NONDET_STRING_STEP(31);
#undef _ESBMC_NONDET_STRING_STEP
    _ESBMC_rand_str[len] = '\0';
    return _ESBMC_rand_str;
}
