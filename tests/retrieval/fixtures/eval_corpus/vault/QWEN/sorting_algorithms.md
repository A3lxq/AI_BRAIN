### USER

Compare quicksort and mergesort.

### ASSISTANT

Both quicksort and mergesort run in O(n log n) average time. Mergesort is stable and guarantees O(n log n) even in the worst case, but requires O(n) extra space for merging. Quicksort sorts in place (O(log n) stack space) and is typically faster in practice due to better cache locality, but its worst case degrades to O(n²) on already-sorted or adversarial input unless a good pivot-selection strategy (like randomization or median-of-three) is used.
