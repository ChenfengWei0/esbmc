#!/bin/bash

# Path to the ESBMC binary
path_to_esbmc=./esbmc

# Verification Witnesses tokenizer
tokenizer_path=./tokenizer

# Global command line, common to all (normal) tests.
global_cmd_line="--no-unwinding-assertions --64 -DLDV_ERROR=ERROR -Dassert=notassert -D_Bool=int --no-bounds-check --no-pointer-check --error-label ERROR --no-div-by-zero-check --no-assertions --quiet --context-switch 4 --state-hashing --force-malloc-success"

# The simple memory model command line is the global, without all the
# safety checks.
memory_cmd_line="--no-unwinding-assertions --64 -DLDV_ERROR=ERROR -Dassert=notassert -D_Bool=int --quiet --context-switch 3 --state-hashing --force-malloc-success --memory-leak-check"

# The '-D' options are a series of workarounds for some problems encountered:
#  -DLDV_ERROR=ERROR  maps the error label in the 'regression' dir to 'ERROR',
#                     so that we don't have to parse the property file,
#  -Dassert=notassert maps the error function in 'seq-mthreaded' directory to
#                     be named 'notassert', as giving a body for the 'assert'
#                     function conflicts with our internal library
#  -D_Bool=int        works around the presence of some booleans inside
#                     bitfields in some linux-based benchmarks.

# Memsafety cmdline picked
do_memsafety=0
do_term=0

while getopts "c:h" arg; do
    case $arg in
        h)
            echo "Usage: $0 [options] path_to_benchmark
Options:
-h             Print this message
-c propfile    Specifythe given property file"
            ;;
        c)
            # Given the lack of variation in the property file... we don't
            # actually interpret it. Instead we have the same options to all
            # tests (except for the memory checking ones), and define all the
            # error labels from other directories to be ERROR.
            if ! grep -q ERROR $OPTARG; then
                do_memsafety=1
            fi
            if ! grep -q 'LTL(F' $OPTARG; then
                do_term=1
            fi
            ;;
    esac
done

shift $(( OPTIND - 1 ));

# Store the path to the file we're going to be checking.
benchmark=$1

if test "${benchmark}" = ""; then
    echo "No benchmark given" #>&2
    exit 1
fi

# Pick the command line to be using
if test ${do_memsafety} = 0; then
    cmdline=${global_cmd_line}
else
    cmdline=${memory_cmd_line}
fi

# Add graphml informations
TMPGRAPHML=`mktemp`
mv $TMPGRAPHML "$TMPGRAPHML.graphml"
TMPGRAPHML="$TMPGRAPHML.graphml"
cmdline="$cmdline --witnesspath $TMPGRAPHML --tokenizer $tokenizer_path"

# Drop all output into a temporary file,
TMPFILE=`mktemp`

# This year we're not iteratively deepening, we're running ESBMC with a fixed
# unwind bound of 16.
${path_to_esbmc} ${cmdline} --unwind 16

# Postprocessing: first, collect some facts
grep "VERIFICATION FAILED" ${TMPFILE} > /dev/null #2>&1
failed=$?
grep "VERIFICATION SUCCESSFUL" ${TMPFILE} > /dev/null #2>&1
success=$?
grep -i "Timed out" ${TMPFILE} > /dev/null #2>&1
timeout=$?

# Decide which result we determined here. The ordering is important: check for
# a counterexample first. The output file may contain both success and failure,
# if a smaller unwind bound didn't uncover the error. But if there's a
# counterexample, then there's an error.
if test $failed = 0; then
    # Error path found
    echo "FALSE"
    echo "Counterexample in graphML format is available in: ${TMPGRAPHML}"
    rm ${TMPFILE}
elif test $success = 0; then
    echo "TRUE"
    # Clean up after ourselves
    rm ${TMPFILE}
elif test $timeout = 0; then
    echo "Timed Out"
    rm ${TMPFILE}
else
    echo "UNKNOWN"
fi
