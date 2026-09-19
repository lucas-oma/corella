import { useRef, type ReactNode } from "react";

/** Pretty-print if the text is valid JSON; leave it alone if it isn't
 * (mid-edit / a template the admin hasn't finished). */
export function beautifyJson(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed) return raw;
  try {
    return JSON.stringify(JSON.parse(trimmed), null, 2);
  } catch {
    return raw;
  }
}

const SECRET_VAR = /\{\{secret\.[A-Za-z][A-Za-z0-9_]*\}\}/;
const TOKEN =
  /(\{\{secret\.[A-Za-z][A-Za-z0-9_]*\}\})|(\{\{[A-Za-z][A-Za-z0-9_]*\}\})|("(?:\\.|[^"\\])*")/g;

function highlightVars(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const re = /(\{\{secret\.[A-Za-z][A-Za-z0-9_]*\}\})|(\{\{[A-Za-z][A-Za-z0-9_]*\}\})/g;
  let last = 0;
  let i = 0;
  let match: RegExpExecArray | null;
  while ((match = re.exec(text))) {
    if (match.index > last) {
      nodes.push(<span key={`${keyPrefix}-t${i++}`}>{text.slice(last, match.index)}</span>);
    }
    if (SECRET_VAR.test(match[0])) {
      nodes.push(
        <span key={`${keyPrefix}-s${i++}`} className="text-status-warning">
          {match[0]}
        </span>,
      );
    } else {
      nodes.push(
        <span key={`${keyPrefix}-v${i++}`} className="text-status-info">
          {match[0]}
        </span>,
      );
    }
    last = match.index + match[0].length;
  }
  if (last < text.length) {
    nodes.push(<span key={`${keyPrefix}-t${i}`}>{text.slice(last)}</span>);
  }
  return nodes;
}

function isKeyString(source: string, afterIndex: number): boolean {
  return /^\s*:/.test(source.slice(afterIndex));
}

/** Color {{secret.NAME}} (amber) and other {{placeholders}} (blue) so they
 * stand out from the surrounding JSON. Keys stay muted. */
export function highlightJsonTemplate(source: string): ReactNode {
  if (!source) return null;
  const nodes: ReactNode[] = [];
  let last = 0;
  let i = 0;
  TOKEN.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = TOKEN.exec(source))) {
    if (match.index > last) {
      nodes.push(<span key={`p${i++}`}>{source.slice(last, match.index)}</span>);
    }
    const token = match[0];
    const end = match.index + token.length;
    if (match[1]) {
      nodes.push(
        <span key={`t${i++}`} className="text-status-warning">
          {token}
        </span>,
      );
    } else if (match[2]) {
      nodes.push(
        <span key={`t${i++}`} className="text-status-info">
          {token}
        </span>,
      );
    } else {
      const key = isKeyString(source, end);
      nodes.push(
        <span key={`q${i++}`} className={key ? "text-ink-muted" : undefined}>
          {highlightVars(token, `q${i}`)}
        </span>,
      );
    }
    last = end;
  }
  if (last < source.length) {
    nodes.push(<span key={`p${i}`}>{source.slice(last)}</span>);
  }
  return nodes;
}

const EDITOR =
  "min-h-[5.5rem] w-full resize-y overflow-auto whitespace-pre-wrap break-words px-3 py-2 font-mono text-[13px] leading-5";

export default function JsonTemplateField({
  value,
  onChange,
  placeholder,
  disabled = false,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  disabled?: boolean;
}) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const overlayRef = useRef<HTMLPreElement>(null);
  const lines = Math.max(4, value.split("\n").length);

  function syncScroll() {
    if (textareaRef.current && overlayRef.current) {
      overlayRef.current.scrollTop = textareaRef.current.scrollTop;
      overlayRef.current.scrollLeft = textareaRef.current.scrollLeft;
    }
  }

  return (
    <div
      className={`field relative p-0 focus-within:border-accent ${disabled ? "opacity-40" : ""}`}
    >
      <pre
        ref={overlayRef}
        aria-hidden
        className={`${EDITOR} pointer-events-none absolute inset-0 m-0 text-ink dark:text-ink-inverted`}
      >
        {value ? (
          highlightJsonTemplate(value)
        ) : (
          <span className="text-ink-subtle">{placeholder}</span>
        )}
        {/* Trailing newline so the overlay height matches a textarea that
            always has an extra empty line at the caret. */}
        {"\n"}
      </pre>
      <textarea
        ref={textareaRef}
        value={value}
        rows={lines}
        disabled={disabled}
        spellCheck={false}
        autoCorrect="off"
        autoCapitalize="off"
        autoComplete="off"
        onChange={(e) => onChange(e.target.value)}
        onBlur={() => onChange(beautifyJson(value))}
        onScroll={syncScroll}
        className={`${EDITOR} relative z-10 border-0 bg-transparent text-transparent caret-ink outline-none selection:bg-accent/20 dark:caret-ink-inverted`}
      />
    </div>
  );
}
