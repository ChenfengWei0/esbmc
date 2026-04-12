/*
 * C equivalent of the minimal reproducer for library_11.
 * Tests: array copy, pointer reassignment, and value preservation.
 * Uses stack allocation to avoid malloc-NULL issues in BMC.
 */
#include <assert.h>

int main() {
    unsigned arr_storage[1] = {42};
    unsigned *arr = arr_storage;

    unsigned newArr_storage[2] = {0, 0};
    unsigned *newArr = newArr_storage;
    for (unsigned i = 0; i < 1; ++i)
        newArr[i] = arr[i];
    newArr[1] = 99;

    /* Reassign pointer */
    arr = newArr;

    /* Value should be preserved after copy + reassignment */
    assert(arr[0] == 42);   /* should PASS */

    return 0;
}
