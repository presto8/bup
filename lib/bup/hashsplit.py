
from __future__ import absolute_import
import io, math, os

from bup import _helpers, compat, helpers
from bup._helpers import HashSplitter, RecordHashSplitter
from bup.compat import buffer, py_maj


BUP_BLOBBITS = 13
BUP_TREE_BLOBBITS = 13
MAX_PER_TREE = 256
progress_callback = None
DEFAULT_FANOUT = 16

GIT_MODE_FILE = 0o100644
GIT_MODE_TREE = 0o40000
GIT_MODE_SYMLINK = 0o120000


def hashsplit_iter(files, keep_boundaries, progress, fanout=None, blobbits=None):
    fanbits = int(math.log(fanout or DEFAULT_FANOUT, 2))
    blobbits = blobbits or BUP_BLOBBITS
    # yield from ...
    for buf, level in _helpers.HashSplitter(files, bits=blobbits, progress=progress,
                                            keep_boundaries=keep_boundaries,
                                            fanbits=fanbits):
        yield buf, level


total_split = 0
def split_to_blobs(makeblob, files, keep_boundaries, progress, fanout=None,
                   blobbits=None):
    global total_split
    for (blob, level) in hashsplit_iter(files, keep_boundaries, progress,
                                        fanout, blobbits):
        sha = makeblob(blob)
        total_split += len(blob)
        if progress_callback:
            progress_callback(len(blob))
        yield (sha, len(blob), level)


def _make_shalist(l):
    ofs = 0
    l = list(l)
    total = sum(size for mode,sha,size, in l)
    vlen = len(b'%x' % total)
    shalist = []
    for (mode, sha, size) in l:
        shalist.append((mode, b'%0*x' % (vlen,ofs), sha))
        ofs += size
    assert(ofs == total)
    return (shalist, total)


def _squish(maketree, stacks, n):
    i = 0
    while i < n or len(stacks[i]) >= MAX_PER_TREE:
        while len(stacks) <= i+1:
            stacks.append([])
        if len(stacks[i]) == 1:
            stacks[i+1] += stacks[i]
        elif stacks[i]:
            (shalist, size) = _make_shalist(stacks[i])
            tree = maketree(shalist)
            stacks[i+1].append((GIT_MODE_TREE, tree, size))
        stacks[i] = []
        i += 1


def split_to_shalist(makeblob, maketree, files,
                     keep_boundaries, progress=None,
                     fanout=None, blobbits=None):
    sl = split_to_blobs(makeblob, files, keep_boundaries, progress,
                        fanout, blobbits)
    assert(fanout != 0)
    if not fanout:
        shal = []
        for (sha,size,level) in sl:
            shal.append((GIT_MODE_FILE, sha, size))
        return _make_shalist(shal)[0]
    else:
        stacks = [[]]
        for (sha,size,level) in sl:
            stacks[0].append((GIT_MODE_FILE, sha, size))
            _squish(maketree, stacks, level)
        #log('stacks: %r\n' % [len(i) for i in stacks])
        _squish(maketree, stacks, len(stacks)-1)
        #log('stacks: %r\n' % [len(i) for i in stacks])
        return _make_shalist(stacks[-1])[0]


def split_to_blob_or_tree(makeblob, maketree, files,
                          keep_boundaries, progress=None,
                          fanout=None, blobbits=None):
    shalist = list(split_to_shalist(makeblob, maketree,
                                    files, keep_boundaries,
                                    progress, fanout, blobbits))
    if len(shalist) == 1:
        return (shalist[0][0], shalist[0][2])
    elif len(shalist) == 0:
        return (GIT_MODE_FILE, makeblob(b''))
    else:
        return (GIT_MODE_TREE, maketree(shalist))


def open_noatime(name):
    fd = _helpers.open_noatime(name)
    try:
        return os.fdopen(fd, 'rb', 1024*1024)
    except:
        try:
            os.close(fd)
        except:
            pass
        raise
