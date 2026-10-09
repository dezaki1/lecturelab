export type DiffPart = { type: 'same' | 'del' | 'ins'; text: string }

/** Word-level diff (LCS) of two short lines, keeping the whitespace that follows each word. */
export function diffWords(before: string, after: string): DiffPart[] {
  const a = before.match(/\S+\s*/g) ?? []
  const b = after.match(/\S+\s*/g) ?? []
  const key = (w: string) => w.trim()
  // lcs[i][j] = length of the LCS of a[i:] and b[j:]
  const lcs = Array.from({ length: a.length + 1 }, () => Array.from({ length: b.length + 1 }, () => 0))
  for (let i = a.length - 1; i >= 0; i--)
    for (let j = b.length - 1; j >= 0; j--)
      lcs[i][j] = key(a[i]) === key(b[j]) ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1])

  const out: DiffPart[] = []
  const push = (type: DiffPart['type'], text: string) => {
    const last = out[out.length - 1]
    if (last?.type === type) last.text += text
    else out.push({ type, text })
  }
  let i = 0
  let j = 0
  while (i < a.length && j < b.length) {
    if (key(a[i]) === key(b[j])) {
      push('same', b[j])
      i++
      j++
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) push('del', a[i++])
    else push('ins', b[j++])
  }
  while (i < a.length) push('del', a[i++])
  while (j < b.length) push('ins', b[j++])
  return out
}
