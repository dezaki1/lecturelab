import { toast } from 'sonner'

/** Download a generated PDF; without Edge/Chrome on the server, open the print view instead. */
export async function downloadPdf(pdfUrl: string, printPath: string): Promise<void> {
  const id = toast.loading('Making the PDF…')
  try {
    const res = await fetch(pdfUrl)
    if (res.status === 501) {
      toast.info('Opening the print view: use “Save as PDF” there.', { id })
      window.open(printPath, '_blank')
      return
    }
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      throw new Error(typeof data.detail === 'string' ? data.detail : res.statusText)
    }
    const name =
      /filename="([^"]+)"/.exec(res.headers.get('content-disposition') ?? '')?.[1] ?? 'lecturelab.pdf'
    const url = URL.createObjectURL(await res.blob())
    const a = document.createElement('a')
    a.href = url
    a.download = name
    document.body.append(a)
    a.click()
    a.remove()
    // Edge copies the file after click() returns: revoking the URL right away saved empty PDFs.
    setTimeout(() => URL.revokeObjectURL(url), 60_000)
    toast.success('PDF ready', { id })
  } catch (e) {
    toast.error((e as Error).message, { id })
  }
}
