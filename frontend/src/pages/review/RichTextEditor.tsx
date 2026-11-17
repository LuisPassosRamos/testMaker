import Subscript from '@tiptap/extension-subscript'
import Superscript from '@tiptap/extension-superscript'
import { EditorContent, useEditor, useEditorState } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import { Bold, Check, Italic, Subscript as SubIcon, Superscript as SupIcon, Underline, X } from 'lucide-react'
import { useRef } from 'react'
import type { Run } from '../../api/types'
import { Button, cx } from '../../components/ui'
import { docToRuns, runsToDoc } from '../../lib/runs'

interface Props {
  runs: Run[]
  label: string
  onCommit: (runs: Run[]) => void
  onCancel: () => void
}

/** Inline rich-text editor limited to the formatting an exam needs. */
export function RichTextEditor({ runs, label, onCommit, onCancel }: Props) {
  const wrapper = useRef<HTMLDivElement>(null)
  const done = useRef(false)
  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        heading: false,
        bulletList: false,
        orderedList: false,
        listItem: false,
        listKeymap: false,
        blockquote: false,
        codeBlock: false,
        code: false,
        horizontalRule: false,
        strike: false,
        link: false,
        dropcursor: false,
        trailingNode: false,
      }),
      Subscript,
      Superscript,
    ],
    content: runsToDoc(runs),
    autofocus: 'end',
    editorProps: {
      attributes: {
        'aria-label': label,
        'aria-multiline': 'true',
        role: 'textbox',
        class: 'tiptap px-3 py-2',
      },
    },
  })
  const state = useEditorState({
    editor,
    selector: ({ editor: e }) => ({
      bold: e?.isActive('bold') ?? false,
      italic: e?.isActive('italic') ?? false,
      underline: e?.isActive('underline') ?? false,
      sup: e?.isActive('superscript') ?? false,
      sub: e?.isActive('subscript') ?? false,
    }),
  })

  const commit = () => {
    if (done.current || !editor) return
    done.current = true
    onCommit(docToRuns(editor.getJSON()))
  }
  const cancel = () => {
    done.current = true
    onCancel()
  }

  const tool = (active: boolean, name: string, icon: React.ReactNode, run: () => void, shortcut: string) => (
    <button
      type="button"
      aria-pressed={active}
      aria-label={`${name} (${shortcut})`}
      title={`${name} (${shortcut})`}
      onMouseDown={(e) => e.preventDefault()}
      onClick={run}
      className={cx('grid size-9 place-items-center rounded-lg', active ? 'bg-brand-700 text-white' : 'text-slate-700 hover:bg-brand-50')}
    >
      {icon}
    </button>
  )

  return (
    <div
      ref={wrapper}
      className="rounded-xl border-2 border-brand-500 bg-white shadow-sm"
      onBlur={(e) => {
        if (!wrapper.current?.contains(e.relatedTarget as Node)) commit()
      }}
      onKeyDown={(e) => {
        // Escape cancels; saving happens with “Pronto” or when focus leaves the editor.
        // (Ctrl/Shift+Enter are TipTap's line-break shortcuts, so they are not reused here.)
        if (e.key === 'Escape') {
          e.preventDefault()
          cancel()
        }
      }}
    >
      <div className="flex flex-wrap items-center gap-1 border-b border-slate-100 px-2 py-1" role="toolbar" aria-label="Formatação">
        {tool(!!state?.bold, 'Negrito', <Bold className="size-4" />, () => editor?.chain().focus().toggleBold().run(), 'Ctrl+B')}
        {tool(!!state?.italic, 'Itálico', <Italic className="size-4" />, () => editor?.chain().focus().toggleItalic().run(), 'Ctrl+I')}
        {tool(!!state?.underline, 'Sublinhado', <Underline className="size-4" />, () => editor?.chain().focus().toggleUnderline().run(), 'Ctrl+U')}
        {tool(!!state?.sup, 'Sobrescrito (x²)', <SupIcon className="size-4" />, () => editor?.chain().focus().toggleSuperscript().run(), 'Ctrl+.')}
        {tool(!!state?.sub, 'Subscrito (H₂O)', <SubIcon className="size-4" />, () => editor?.chain().focus().toggleSubscript().run(), 'Ctrl+,')}
        <span className="ml-auto flex gap-1">
          <Button size="sm" variant="ghost" icon={<X className="size-4" />} onMouseDown={(e) => e.preventDefault()} onClick={cancel}>
            Cancelar
          </Button>
          <Button size="sm" icon={<Check className="size-4" />} onMouseDown={(e) => e.preventDefault()} onClick={commit}>
            Pronto
          </Button>
        </span>
      </div>
      <EditorContent editor={editor} />
    </div>
  )
}
