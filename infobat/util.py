def gen_shuffle(iter_obj):
    sample = range(len(iter_obj))
    while sample:
        n = sample.pop(random.randrange(len(sample)))
        yield iter_obj[n]
