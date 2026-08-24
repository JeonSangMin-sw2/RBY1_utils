type LegacyFileEntry = {
  isFile: true;
  isDirectory: false;
  name?: string;
  file: (success: (file: File) => void, failure?: (error: DOMException) => void) => void;
};

type LegacyDirectoryReader = {
  readEntries: (
    success: (entries: LegacyEntry[]) => void,
    failure?: (error: DOMException) => void,
  ) => void;
};

type LegacyDirectoryEntry = {
  isFile: false;
  isDirectory: true;
  name?: string;
  createReader: () => LegacyDirectoryReader;
};

type LegacyEntry = LegacyFileEntry | LegacyDirectoryEntry;

type FileHandle = {
  kind: "file";
  name?: string;
  getFile: () => Promise<File>;
};

type DirectoryHandle = {
  kind: "directory";
  name?: string;
  values: () => AsyncIterableIterator<FileHandle | DirectoryHandle>;
};

type DroppedItem = DataTransferItem & {
  webkitGetAsEntry?: () => LegacyEntry | null;
  getAsFileSystemHandle?: () => Promise<FileHandle | DirectoryHandle | null>;
};

const SUPPORTED_SOURCE = /\.(?:log|csv|zip|tar|gz|tgz)$/i;
type CollectedFile = { file: File; path: string };
export type DroppedFiles = { files: File[]; readErrorCount: number };

export function isSupportedSource(file: File): boolean {
  return SUPPORTED_SOURCE.test(file.name);
}

function legacyFile(entry: LegacyFileEntry): Promise<File> {
  return new Promise((resolve, reject) => entry.file(resolve, reject));
}

function legacyEntries(reader: LegacyDirectoryReader): Promise<LegacyEntry[]> {
  return new Promise((resolve, reject) => reader.readEntries(resolve, reject));
}

function nestedPath(parent: string, name: string): string {
  return parent ? `${parent}/${name}` : name;
}

async function walkLegacyEntry(
  entry: LegacyEntry,
  files: CollectedFile[],
  errors: unknown[],
  parent = "",
): Promise<void> {
  if (entry.isFile) {
    try {
      const file = await legacyFile(entry);
      if (isSupportedSource(file)) files.push({ file, path: nestedPath(parent, entry.name || file.name) });
    } catch (error) {
      errors.push(error);
    }
    return;
  }

  const reader = entry.createReader();
  const directory = entry.name ? nestedPath(parent, entry.name) : parent;
  while (true) {
    let entries: LegacyEntry[];
    try {
      entries = await legacyEntries(reader);
    } catch (error) {
      errors.push(error);
      return;
    }
    if (!entries.length) return;
    for (const child of entries) await walkLegacyEntry(child, files, errors, directory);
  }
}

async function walkFileSystemHandle(
  handle: FileHandle | DirectoryHandle,
  files: CollectedFile[],
  errors: unknown[],
  parent = "",
): Promise<void> {
  if (handle.kind === "file") {
    try {
      const file = await handle.getFile();
      if (isSupportedSource(file)) files.push({ file, path: nestedPath(parent, handle.name || file.name) });
    } catch (error) {
      errors.push(error);
    }
    return;
  }

  const directory = handle.name ? nestedPath(parent, handle.name) : parent;
  try {
    for await (const child of handle.values()) await walkFileSystemHandle(child, files, errors, directory);
  } catch (error) {
    errors.push(error);
  }
}

export function filterSupportedFiles(files: Iterable<File>): File[] {
  return Array.from(files).filter(isSupportedSource);
}

function uniqueFiles(files: CollectedFile[]): File[] {
  const seen = new Set<string>();
  return files.flatMap(({ file, path }) => {
    const key = `${path}:${file.size}:${file.lastModified}`;
    if (seen.has(key)) return [];
    seen.add(key);
    return [file];
  });
}

export async function collectDroppedFiles(dataTransfer: DataTransfer): Promise<DroppedFiles> {
  const files: CollectedFile[] = [];
  const errors: unknown[] = [];
  const items = Array.from(dataTransfer.items ?? []) as DroppedItem[];

  for (const item of items) {
    if (item.kind !== "file") continue;
    const entry = item.webkitGetAsEntry?.() as LegacyEntry | null | undefined;
    if (entry) {
      await walkLegacyEntry(entry, files, errors);
      continue;
    }

    let handle: FileHandle | DirectoryHandle | null | undefined;
    try {
      handle = await item.getAsFileSystemHandle?.();
    } catch (error) {
      errors.push(error);
    }
    if (handle) {
      await walkFileSystemHandle(handle, files, errors);
      continue;
    }

    const file = item.getAsFile();
    if (file && isSupportedSource(file)) files.push({ file, path: file.webkitRelativePath || file.name });
  }

  files.push(...filterSupportedFiles(dataTransfer.files ?? []).map((file) => ({
    file,
    path: file.webkitRelativePath || file.name,
  })));

  const result = uniqueFiles(files).sort((left, right) => {
    const leftPath = left.webkitRelativePath || left.name;
    const rightPath = right.webkitRelativePath || right.name;
    return leftPath.localeCompare(rightPath, "ko", { numeric: true });
  });
  if (!result.length && errors.length) throw new Error("폴더 항목을 읽지 못했습니다.");
  return { files: result, readErrorCount: errors.length };
}
