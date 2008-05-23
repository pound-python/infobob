#!/usr/bin/python
import sys, re, gdbm, collections
ORDER = 4
_words_regex = re.compile(r"(^\*)?[a-zA-Z',.!?\-:; ]")

def main():
    chains = collections.defaultdict(list)
    def append_chain(chain, v):
        chains[chain].append(v)
    start_offset = actions = 0
    lines_parsed = 0
    for line in sys.stdin:
        if not line: continue
        lines_parsed += 1
        if lines_parsed % 500 == 0:
            print lines_parsed
        queue, length = '', 0
        for w in _words_regex.finditer(line):
            w = w.group().lower()
            if not queue and w == ' ': continue
            if len(queue) == ORDER and not queue.startswith('*'):
                append_chain(queue, w)
            if w[-1] in '.!?':
                queue, length = '', 0
            else:
                queue += w
                length += 1
                if length == ORDER:
                    if queue.startswith('*'):
                        append_chain('__act__', queue.lstrip('*'))
                        actions += 1
                    else:
                    	if len(queue) != ORDER: print `queue`
                        append_chain('__start__', queue)
                        start_offset += 1
                queue = queue[-ORDER:]
    chains['__offset__'] = '%d;%d' % (start_offset, actions)
    chainsdb = gdbm.open(sys.argv[1], 'nf')
    for k in chains:
        chainsdb[k] = ''.join(chains[k])
    chainsdb.sync()
if __name__ == '__main__': main()