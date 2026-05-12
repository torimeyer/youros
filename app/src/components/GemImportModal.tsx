import { useState, useEffect, useRef } from 'react';
import Icon from './Icon';
import { api } from '../lib/api';
import type { Gem } from '../pages/MyGems';

interface Props {
  gem?: Gem | null;
  onClose: () => void;
  onSaved: () => void;
}

const ALLOWED_EXTENSIONS = ['.txt', '.md', '.pdf', '.docx'];
const MAX_SIZE_MB = 5;

export default function GemImportModal({ gem, onClose, onSaved }: Props) {
  const isEdit = gem != null;

  const [name, setName] = useState(gem?.name ?? '');
  const [systemPrompt, setSystemPrompt] = useState(gem?.system_prompt ?? '');
  const [knowledgeFiles, setKnowledgeFiles] = useState<string[]>(gem?.knowledge_files ?? []);

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const [dragOver, setDragOver] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState('');
  const fileInputRef = useRef<HTMLInputElement>(null);

  const nameRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    nameRef.current?.focus();
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const uploadFile = async (file: File) => {
    const ext = '.' + (file.name.split('.').pop()?.toLowerCase() ?? '');
    if (!ALLOWED_EXTENSIONS.includes(ext)) {
      setUploadError(`Only ${ALLOWED_EXTENSIONS.join(', ')} files are supported.`);
      return;
    }
    if (file.size > MAX_SIZE_MB * 1024 * 1024) {
      setUploadError(`File must be under ${MAX_SIZE_MB} MB.`);
      return;
    }
    setUploadError('');
    setUploading(true);
    try {
      const form = new FormData();
      form.append('file', file);
      const res = await fetch('/api/gems/upload', {
        method: 'POST',
        body: form,
        credentials: 'include',
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setUploadError(data.detail || 'Upload failed.');
        return;
      }
      const data = await res.json();
      setKnowledgeFiles((prev) => [...prev, data.filename]);
    } catch {
      setUploadError('Upload failed. Check your connection and try again.');
    } finally {
      setUploading(false);
    }
  };

  const handleFiles = (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const arr = Array.from(files);
    (async () => { for (const f of arr) await uploadFile(f); })();
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    handleFiles(e.dataTransfer.files);
  };

  const removeFile = (filename: string) => {
    setKnowledgeFiles((prev) => prev.filter((f) => f !== filename));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    if (!systemPrompt.trim()) return;

    setSubmitting(true);
    setSubmitError(null);
    try {
      if (isEdit) {
        await api.patch(`/gems/${gem!.id}`, { name: name.trim(), system_prompt: systemPrompt.trim(), knowledge_files: knowledgeFiles });
      } else {
        await api.post('/gems', { name: name.trim(), system_prompt: systemPrompt.trim(), knowledge_files: knowledgeFiles });
      }
      onSaved();
    } catch {
      setSubmitError('Could not save. Check your connection and try again.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={isEdit ? `Edit ${gem!.name}` : 'Create Gem'}
      data-testid="gem-import-modal"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="w-full max-w-lg bg-slate-900 border border-slate-700 rounded-xl shadow-2xl flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-800">
          <div className="flex items-center gap-2">
            <Icon name="auto_awesome" size={16} className="text-blue-400" />
            <span className="text-sm font-semibold text-white">
              {isEdit ? 'Edit Gem' : 'Create Gem'}
            </span>
          </div>
          <button
            data-testid="gem-modal-close"
            onClick={onClose}
            className="text-slate-500 hover:text-white transition-colors"
            aria-label="Close"
          >
            <Icon name="close" size={16} />
          </button>
        </div>

        {/* Body */}
        <form
          onSubmit={handleSubmit}
          className="flex flex-col gap-4 px-5 py-4 overflow-y-auto"
          data-testid="gem-import-form"
        >
          {/* Name */}
          <div>
            <label htmlFor="gem-name" className="block text-sm text-slate-300 mb-1.5 font-medium">
              Name <span className="text-red-400">*</span>
            </label>
            <input
              ref={nameRef}
              id="gem-name"
              data-testid="gem-name-input"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Code reviewer, Research assistant"
              required
              className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500/40 transition-colors"
            />
          </div>

          {/* System prompt */}
          <div>
            <label htmlFor="gem-system-prompt" className="block text-sm text-slate-300 mb-1.5 font-medium">
              Instructions <span className="text-red-400">*</span>
            </label>
            <p className="text-xs text-slate-500 mb-2">
              Tell Gemini what role to play, what to focus on, and how to respond. Paste from Gemini&apos;s Gem editor if you have one.
            </p>
            <textarea
              id="gem-system-prompt"
              data-testid="gem-system-prompt-input"
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              placeholder="You are a helpful assistant that..."
              required
              rows={6}
              className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500/40 transition-colors resize-y min-h-[100px]"
            />
          </div>

          {/* Knowledge files */}
          <div>
            <label className="block text-sm text-slate-300 mb-1.5 font-medium">
              Knowledge files <span className="text-slate-500 font-normal">(optional)</span>
            </label>
            <p className="text-xs text-slate-500 mb-2">
              Files you upload become context Gemini can draw on during the chat. Supported: .txt, .md, .pdf, .docx (max {MAX_SIZE_MB} MB each).
            </p>

            <div
              data-testid="gem-drop-zone"
              onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
              onDragLeave={() => setDragOver(false)}
              onDrop={handleDrop}
              onClick={() => fileInputRef.current?.click()}
              className={`border-2 border-dashed rounded-lg px-4 py-4 text-center cursor-pointer transition-colors ${
                dragOver
                  ? 'border-blue-500 bg-blue-500/10'
                  : 'border-slate-700 hover:border-slate-500 bg-slate-800/50'
              }`}
            >
              <Icon
                name={uploading ? 'hourglass_empty' : 'upload_file'}
                className="text-2xl text-slate-500 mb-1"
              />
              <p className="text-sm text-slate-400">
                {uploading ? 'Uploading...' : 'Click to browse or drag a file here'}
              </p>
            </div>

            <input
              ref={fileInputRef}
              type="file"
              accept={ALLOWED_EXTENSIONS.join(',')}
              multiple
              className="hidden"
              onChange={(e) => { handleFiles(e.target.files); if (fileInputRef.current) fileInputRef.current.value = ''; }}
              disabled={uploading}
              data-testid="gem-file-input"
            />

            {uploadError && (
              <p data-testid="gem-upload-error" className="text-xs text-red-400 mt-2">{uploadError}</p>
            )}

            {knowledgeFiles.length > 0 && (
              <ul className="mt-2 space-y-1" data-testid="gem-file-list">
                {knowledgeFiles.map((f) => (
                  <li
                    key={f}
                    className="flex items-center justify-between bg-slate-800 rounded-lg px-3 py-2 text-sm text-slate-300"
                    data-testid={`gem-file-item-${f}`}
                  >
                    <span className="flex items-center gap-2 truncate">
                      <Icon name="description" className="text-base text-slate-500" />
                      <span className="truncate">{f}</span>
                    </span>
                    <button
                      type="button"
                      onClick={() => removeFile(f)}
                      className="text-slate-500 hover:text-red-400 transition-colors ml-2 shrink-0"
                      aria-label={`Remove ${f}`}
                      data-testid={`gem-file-remove-${f}`}
                    >
                      <Icon name="close" size={14} />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {submitError && (
            <p data-testid="gem-submit-error" className="text-sm text-red-400">{submitError}</p>
          )}

          {/* Footer buttons */}
          <div className="flex items-center justify-end gap-2 pt-1 pb-1">
            <button
              type="button"
              data-testid="gem-modal-cancel"
              onClick={onClose}
              className="px-4 py-2 text-slate-400 hover:text-white text-sm rounded-lg transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              data-testid="gem-modal-submit"
              disabled={submitting || !name.trim() || !systemPrompt.trim()}
              className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm rounded-lg transition-colors font-medium"
            >
              {submitting ? 'Saving...' : isEdit ? 'Save changes' : 'Create Gem'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
