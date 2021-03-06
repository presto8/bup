#!/bin/sh
"""": # -*-python-*-
bup_python="$(dirname "$0")/bup-python" || exit $?
exec "$bup_python" "$0" ${1+"$@"}
"""
# end of bup preamble

from __future__ import absolute_import, division, print_function
from binascii import hexlify
import os, sys, time

from bup import hashsplit, git, options, client, repo
from bup.compat import argv_bytes, environ
from bup.helpers import (add_error, handle_ctrl_c, hostname, log, parse_num,
                         qprogress, reprogress, saved_errors,
                         valid_save_name,
                         parse_date_or_fatal)
from bup.io import byte_stream
from bup.pwdgrp import userfullname, username


optspec = """
bup split [-t] [-c] [-n name] OPTIONS [--git-ids | filenames...]
bup split -b OPTIONS [--git-ids | filenames...]
bup split --copy OPTIONS [--git-ids | filenames...]
bup split --noop [-b|-t] OPTIONS [--git-ids | filenames...]
--
 Modes:
b,blobs    output a series of blob ids.  Ignores --fanout.
t,tree     output a tree id
c,commit   output a commit id
n,name=    save the result under the given name
noop       split the input, but throw away the result
copy       split the input, copy it to stdout, don't save to repo
 Options:
r,remote=  remote repository path
d,date=    date for the commit (seconds since the epoch)
q,quiet    don't print progress messages
v,verbose  increase log output (can be used more than once)
git-ids    read a list of git object ids from stdin and split their contents
keep-boundaries  don't let one chunk span two input files
bench      print benchmark timings to stderr
max-pack-size=  maximum bytes in a single pack
max-pack-objects=  maximum number of objects in a single pack
fanout=    average number of blobs in a single tree
bwlimit=   maximum bytes/sec to transmit to server
#,compress=  set compression level to # (0-9, 9 is highest)
blobbits=  number of bits for blob splitting (see man page)
"""
handle_ctrl_c()

o = options.Options(optspec)
(opt, flags, extra) = o.parse(sys.argv[1:])
if opt.name: opt.name = argv_bytes(opt.name)
if opt.remote: opt.remote = argv_bytes(opt.remote)
if opt.verbose is None: opt.verbose = 0

if not (opt.blobs or opt.tree or opt.commit or opt.name or
        opt.noop or opt.copy):
    o.fatal("use one or more of -b, -t, -c, -n, --noop, --copy")
if opt.copy and (opt.blobs or opt.tree):
    o.fatal('--copy is incompatible with -b, -t')
if (opt.noop or opt.copy) and (opt.commit or opt.name):
    o.fatal('--noop and --copy are incompatible with -c, -n')
if opt.blobs and (opt.tree or opt.commit or opt.name):
    o.fatal('-b is incompatible with -t, -c, -n')
if extra and opt.git_ids:
    o.fatal("don't provide filenames when using --git-ids")

if opt.verbose >= 2:
    git.verbose = opt.verbose - 1
    opt.bench = 1

fanout = None
if opt.fanout:
    # This used to be in hashsplit, but that's just confusing;
    # hashsplit now defaults to the real default (16) if 0 (or
    # None) is passed, but keep the command-line compatible...
    fanout = parse_num(opt.fanout) or 128
blobbits = None
if opt.blobbits:
    blobbits = parse_num(opt.blobbits)
if opt.bwlimit:
    client.bwlimit = parse_num(opt.bwlimit)
if opt.date:
    date = parse_date_or_fatal(opt.date, o.fatal)
else:
    date = time.time()

total_bytes = 0
def prog(filenum, nbytes):
    global total_bytes
    total_bytes += nbytes
    if filenum > 0:
        qprogress('Splitting: file #%d, %d kbytes\r'
                  % (filenum+1, total_bytes // 1024))
    else:
        qprogress('Splitting: %d kbytes\r' % (total_bytes // 1024))


start_time = time.time()

if opt.name and not valid_save_name(opt.name):
    o.fatal("'%r' is not a valid branch name." % opt.name)
refname = opt.name and b'refs/heads/%s' % opt.name or None

if opt.noop or opt.copy:
    repo = oldref = None
else:
    repo = repo.from_opts(opt)
    oldref = refname and repo.read_ref(refname) or None
    repobits = repo.config(b'bup.blobbits', opttype='int') or hashsplit.BUP_BLOBBITS
    if not blobbits:
        blobbits = repobits
    else:
        print("overriding repo blobbits %d from cmdline with %d" % (repobits, blobbits))

input = byte_stream(sys.stdin)

if opt.git_ids:
    # the input is actually a series of git object ids that we should retrieve
    # and split.
    #
    # This is a bit messy, but basically it converts from a series of
    # CatPipe.get() iterators into a series of file-type objects.
    # It would be less ugly if either CatPipe.get() returned a file-like object
    # (not very efficient), or split_to_shalist() expected an iterator instead
    # of a file.
    cp = git.CatPipe()
    class IterToFile:
        def __init__(self, it):
            self.it = iter(it)
        def read(self, size):
            v = next(self.it, None)
            return v or b''
    def read_ids():
        while 1:
            line = input.readline()
            if not line:
                break
            if line:
                line = line.strip()
            try:
                it = cp.get(line.strip())
                next(it, None)  # skip the file info
            except KeyError as e:
                add_error('error: %s' % e)
                continue
            yield IterToFile(it)
    files = read_ids()
else:
    # the input either comes from a series of files or from stdin.
    files = extra and (open(argv_bytes(fn), 'rb') for fn in extra) or [input]

if repo:
    write_data = repo.write_data
    write_tree = repo.write_tree
elif opt.blobs or opt.tree:
    # --noop mode
    write_data = lambda content: git.calc_hash(b'blob', content)
    write_tree = lambda shalist: git.calc_hash(b'tree', git.tree_encode(shalist))

sys.stdout.flush()
out = byte_stream(sys.stdout)

if opt.blobs:
    shalist = hashsplit.split_to_blobs(write_data, files,
                                       keep_boundaries=opt.keep_boundaries,
                                       progress=prog, blobbits=blobbits)
    for (sha, size, level) in shalist:
        out.write(hexlify(sha) + b'\n')
        reprogress()
elif opt.tree or opt.commit or opt.name:
    if opt.name: # insert dummy_name which may be used as a restore target
        mode, sha = \
            hashsplit.split_to_blob_or_tree(write_data, write_tree, files,
                                            keep_boundaries=opt.keep_boundaries,
                                            progress=prog, fanout=fanout,
                                            blobbits=blobbits)
        splitfile_name = git.mangle_name(b'data', hashsplit.GIT_MODE_FILE, mode)
        shalist = [(mode, splitfile_name, sha)]
    else:
        shalist = hashsplit.split_to_shalist(
                      write_data, write_tree, files,
                      keep_boundaries=opt.keep_boundaries,
                      progress=prog, fanout=fanout,
                      blobbits=blobbits)
    tree = write_tree(shalist)
else:
    last = 0
    it = hashsplit.hashsplit_iter(files,
                                  keep_boundaries=opt.keep_boundaries,
                                  progress=prog, fanout=fanout,
                                  blobbits=blobbits)
    for (blob, level) in it:
        hashsplit.total_split += len(blob)
        if opt.copy:
            sys.stdout.write(str(blob))
        megs = hashsplit.total_split // 1024 // 1024
        if not opt.quiet and last != megs:
            last = megs

if opt.verbose:
    log('\n')
if opt.tree:
    out.write(hexlify(tree) + b'\n')
if opt.commit or opt.name:
    msg = b'bup split\n\nGenerated by command:\n%r\n' % sys.argv
    ref = opt.name and (b'refs/heads/%s' % opt.name) or None
    userline = b'%s <%s@%s>' % (userfullname(), username(), hostname())
    commit = repo.write_commit(tree, oldref, userline, date, None,
                               userline, date, None, msg)
    if opt.commit:
        out.write(hexlify(commit) + b'\n')

if opt.name and repo:
    repo.update_ref(refname, commit, oldref)

if repo:
    repo.close()

secs = time.time() - start_time
size = hashsplit.total_split
if opt.bench:
    log('bup: %.2f kbytes in %.2f secs = %.2f kbytes/sec\n'
        % (size / 1024, secs, size / 1024 / secs))

if saved_errors:
    log('WARNING: %d errors encountered while saving.\n' % len(saved_errors))
    sys.exit(1)
