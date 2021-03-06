#!/usr/bin/env bash
. ./wvtest-bup.sh || exit $?
. t/lib.sh || exit $?

set -o pipefail

top="$(WVPASS pwd)" || exit $?
tmpdir="$(WVPASS wvmktempdir)" || exit $?

export BUP_DIR="$tmpdir/bup"
export GIT_DIR="$tmpdir/bup"

bup() { "$top/bup" "$@"; }

export TZ=UTC

WVPASS bup init
WVPASS cd "$tmpdir"

WVPASS mkdir src

# minimum needed for splitting into 2 levels at % 5 below
NFILES=26

FILELIST=$(for f in $(seq $NFILES) ; do echo $(printf %04d%04d $f $f) ; done)

for f in $FILELIST ; do
    c=2${f:1:3}
    touch -t "${c}01010000" src/$f
done
touch -t 199901010000 src

WVPASS git config --add --bool bup.treesplit true
WVPASS bup index src

# override the hash-splitter so we can do multiple levels
# without creating a few hundred thousand files ...
cat > "$tmpdir/bup-save" << EOF
#!/usr/bin/env $top/cmd/bup-python
from bup import _helpers

class RecordHashSplitter:
    def __init__(self, bits=None):
        self.idx = 0
    def feed(self, name):
        self.idx += 1
        return self.idx % 5 == 0, 20 # second value is ignored
_helpers.RecordHashSplitter = RecordHashSplitter

exec(open("$top/cmd/bup-save", "rb").read())
EOF
chmod +x "$tmpdir/bup-save"

WVPASS "$tmpdir/bup-save" -n src -d 242312160 --strip src

WVSTART "check stored"
WVPASSEQ "$(WVPASS bup ls /)" "src"
# --file-type forces reading with metadata
WVPASSEQ "$(WVPASS bup ls --file-type /)" "src/"
WVPASSEQ "$(WVPASS bup ls /src/latest/)" "$FILELIST"

# check that all the metadata matches up correctly
lsout="$(bup ls -l /src/latest/)"
for f in $FILELIST ; do
    c=2${f:1:3}
    echo "$lsout" | WVPASS grep "${c}-01-01 00:00 $f"
done
bup ls -ld /src/latest/ | WVPASS grep '1999-01-01 00:00 /src/latest'
bup ls -lan /src/latest/ | WVFAIL grep 1970-01-01

WVPASS test "$(git ls-tree --name-only src |grep -v '^\.bupm' | wc -l)" -lt $NFILES
WVPASSEQ "$(git ls-tree --name-only src 2.bupd)" "2.bupd"
# git should be able to list the folders
WVPASSEQ "$(git ls-tree --name-only src 000/0001/00010001)" "000/0001/00010001"
WVPASSEQ "$(git ls-tree --name-only src 002/0026/00260026)" "002/0026/00260026"
WVPASSEQ "$(git ls-tree --name-only src 002/0026/.bupm)" "002/0026/.bupm"
WVPASSEQ "$(git ls-tree --name-only src 002/.bupm)" ""

WVSTART "clean up"
WVPASS rm -rf "$tmpdir"
