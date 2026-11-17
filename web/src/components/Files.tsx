import { useEffect, useState } from "react";
import { fileSize } from "../lib";
import type { AttachmentInfo } from "../types";
import { Dialog } from "../ui";

type Fetch = (file: AttachmentInfo) => Promise<Blob>;

/** An image shown from a fetched blob: never a URL the browser fetches by itself. */
function Thumbnail({ file, fetchFile }: { file: AttachmentInfo; fetchFile: Fetch }) {
  const [url, setUrl] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    let created: string | null = null;
    let live = true;
    fetchFile(file)
      .then((blob) => {
        if (!live) return;
        created = URL.createObjectURL(blob);
        setUrl(created);
      })
      .catch(() => undefined);
    return () => {
      live = false;
      if (created) URL.revokeObjectURL(created);
    };
  }, [file, fetchFile]);
  if (!url) return <span className="file-thumb file-thumb-loading" aria-hidden="true" />;
  return (
    <>
      <button className="file-thumb" onClick={() => setOpen(true)} aria-label={`View ${file.filename}`}>
        <img src={url} alt={file.filename} />
      </button>
      {open && (
        <Dialog title={file.filename} onClose={() => setOpen(false)}>
          <img className="file-full" src={url} alt={file.filename} />
        </Dialog>
      )}
    </>
  );
}

async function save(file: AttachmentInfo, fetchFile: Fetch) {
  const url = URL.createObjectURL(await fetchFile(file));
  const link = document.createElement("a");
  link.href = url;
  link.download = file.filename;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/**
 * Files on a message. Until the malware scan has cleared a file it cannot be
 * opened; a blocked file says so and offers nothing.
 */
export function FileList({ files, fetchFile, canOpen }: { files: AttachmentInfo[]; fetchFile: Fetch; canOpen: boolean }) {
  if (files.length === 0) return null;
  return (
    <ul className="files">
      {files.map((file) => {
        const image = file.content_type.startsWith("image/");
        const ready = file.status === "clean" && canOpen;
        return (
          <li key={file.id} className={`file file-${file.status}`}>
            {ready && image && <Thumbnail file={file} fetchFile={fetchFile} />}
            <span className="file-meta">
              {ready ? (
                <button className="link-button" onClick={() => void save(file, fetchFile)}>
                  {file.filename}
                </button>
              ) : (
                <span>{file.filename}</span>
              )}
              <span className="muted small">
                {file.status === "scanning"
                  ? "Checking for malware…"
                  : file.status === "blocked"
                    ? "Blocked: the malware scanner found a threat"
                    : canOpen
                      ? fileSize(file.size)
                      : `${fileSize(file.size)} · your role cannot open files`}
              </span>
            </span>
          </li>
        );
      })}
    </ul>
  );
}
