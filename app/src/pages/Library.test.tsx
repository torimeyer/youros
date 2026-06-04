import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import Library from './Library';
import { api } from '../lib/api';

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    delete: vi.fn(),
  },
}));

vi.mock('../components/TopBar', () => ({
  default: () => <div data-testid="topbar">Library</div>,
}));

const mockSources = [
  {
    id: 'abc123',
    title: 'brand-guide.pdf',
    type: 'pdf',
    size: 20480,
    tags: ['brand'],
    source_url: '',
    upload_time: '2026-05-20T10:00:00Z',
  },
  {
    id: 'def456',
    title: 'icp.md',
    type: 'md',
    size: 4096,
    tags: ['icp'],
    source_url: '',
    upload_time: '2026-05-19T09:00:00Z',
  },
];

beforeEach(() => {
  vi.clearAllMocks();
});

describe('Library page', () => {
  it('renders the page title', async () => {
    vi.mocked(api.get).mockResolvedValue({ sources: [] });
    render(<Library />);
    expect(screen.getByTestId('topbar')).toHaveTextContent('Library');
  });

  it('shows empty state when no sources', async () => {
    vi.mocked(api.get).mockResolvedValue({ sources: [] });
    render(<Library />);
    await waitFor(() => expect(screen.getByText(/No sources yet/i)).toBeInTheDocument());
  });

  it('lists uploaded sources', async () => {
    vi.mocked(api.get).mockResolvedValue({ sources: mockSources });
    render(<Library />);
    await waitFor(() => expect(screen.getByText('brand-guide.pdf')).toBeInTheDocument());
    expect(screen.getByText('icp.md')).toBeInTheDocument();
  });

  it('shows type labels', async () => {
    vi.mocked(api.get).mockResolvedValue({ sources: mockSources });
    render(<Library />);
    await waitFor(() => expect(screen.getByText('PDF')).toBeInTheDocument());
    expect(screen.getByText('Markdown')).toBeInTheDocument();
  });

  it('shows tags as chips', async () => {
    vi.mocked(api.get).mockResolvedValue({ sources: mockSources });
    render(<Library />);
    await waitFor(() => expect(screen.getByText('brand')).toBeInTheDocument());
    expect(screen.getByText('icp')).toBeInTheDocument();
  });

  it('shows upload button', async () => {
    vi.mocked(api.get).mockResolvedValue({ sources: [] });
    render(<Library />);
    expect(screen.getByText(/Upload file or URL/i)).toBeInTheDocument();
  });

  it('opens upload modal on button click', async () => {
    vi.mocked(api.get).mockResolvedValue({ sources: [] });
    render(<Library />);
    fireEvent.click(screen.getByText(/Upload file or URL/i));
    await waitFor(() => expect(screen.getByTestId('upload-modal')).toBeInTheDocument());
  });

  it('shows remove button per row and confirms inline', async () => {
    vi.mocked(api.get).mockResolvedValue({ sources: mockSources });
    render(<Library />);
    const removeBtns = await screen.findAllByText('Remove');
    expect(removeBtns.length).toBe(2);
    fireEvent.click(removeBtns[0]);
    expect(screen.getByText('Confirm')).toBeInTheDocument();
    expect(screen.getByText('Cancel')).toBeInTheDocument();
  });

  it('deletes a source on confirm', async () => {
    vi.mocked(api.get).mockResolvedValue({ sources: mockSources });
    vi.mocked(api.delete).mockResolvedValue({ result: 'ok' });
    render(<Library />);
    const removeBtns = await screen.findAllByText('Remove');
    fireEvent.click(removeBtns[0]);
    fireEvent.click(screen.getByText('Confirm'));
    await waitFor(() => expect(api.delete).toHaveBeenCalledWith('/sources/abc123'));
  });
});

describe('Upload modal', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockResolvedValue({ sources: [] });
  });

  it('has a tags input defaulting to brand', async () => {
    render(<Library />);
    fireEvent.click(screen.getByText(/Upload file or URL/i));
    const tagsInput = await screen.findByTestId('tags-input');
    expect((tagsInput as HTMLInputElement).value).toBe('brand');
  });

  it('shows file input in file mode', async () => {
    render(<Library />);
    fireEvent.click(screen.getByText(/Upload file or URL/i));
    expect(await screen.findByTestId('file-input')).toBeInTheDocument();
  });

  it('switches to URL mode', async () => {
    render(<Library />);
    fireEvent.click(screen.getByText(/Upload file or URL/i));
    fireEvent.click(await screen.findByText('Web page URL'));
    expect(await screen.findByTestId('url-input')).toBeInTheDocument();
  });

  it('shows error if no file selected on submit', async () => {
    render(<Library />);
    fireEvent.click(screen.getByText(/Upload file or URL/i));
    const submit = await screen.findByTestId('upload-submit');
    fireEvent.click(submit);
    await waitFor(() => expect(screen.getByText(/Pick a file first/i)).toBeInTheDocument());
  });
});
