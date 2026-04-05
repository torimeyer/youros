//! Full passthrough FUSE filesystem (→807).
//!
//! Maps FUSE inodes to real filesystem paths via an inode table.
//! Supports: lookup, getattr, setattr, readdir, open, read, write,
//! create, mkdir, unlink, rmdir, rename.

use fuser::{
    BsdFileFlags, Errno, FileAttr, FileHandle, FileType, Filesystem, FopenFlags, Generation,
    INodeNo, RenameFlags, ReplyAttr, ReplyCreate, ReplyData, ReplyDirectory, ReplyEmpty,
    ReplyEntry, ReplyOpen, ReplyWrite, Request, WriteFlags,
};

use std::collections::HashMap;
use std::ffi::OsStr;
use std::fs::{self, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::os::unix::fs::{MetadataExt, PermissionsExt};
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;
use std::time::{Duration, SystemTime, UNIX_EPOCH};


const TTL: Duration = Duration::from_secs(1);

/// Thread-safe inode table: maps FUSE inode numbers to real filesystem paths.
struct InodeTable {
    by_ino: HashMap<u64, PathBuf>,
    by_path: HashMap<PathBuf, u64>,
    next_ino: AtomicU64,
}

impl InodeTable {
    fn new(root: PathBuf) -> Self {
        let mut by_ino = HashMap::new();
        let mut by_path = HashMap::new();
        by_ino.insert(1, root.clone()); // ROOT = inode 1
        by_path.insert(root, 1);
        Self {
            by_ino,
            by_path,
            next_ino: AtomicU64::new(2),
        }
    }

    fn get_path(&self, ino: u64) -> Option<PathBuf> {
        self.by_ino.get(&ino).cloned()
    }

    fn get_or_insert(&mut self, path: PathBuf) -> u64 {
        if let Some(&ino) = self.by_path.get(&path) {
            return ino;
        }
        let ino = self.next_ino.fetch_add(1, Ordering::Relaxed);
        self.by_ino.insert(ino, path.clone());
        self.by_path.insert(path, ino);
        ino
    }

    fn remove(&mut self, path: &PathBuf) {
        if let Some(ino) = self.by_path.remove(path) {
            self.by_ino.remove(&ino);
        }
    }

    fn rename(&mut self, old: &PathBuf, new: PathBuf) {
        if let Some(ino) = self.by_path.remove(old) {
            self.by_ino.insert(ino, new.clone());
            self.by_path.insert(new, ino);
        }
    }
}

pub struct OstkFs {
    pub source: PathBuf,
    inodes: Mutex<InodeTable>,
}

impl OstkFs {
    pub fn new(source: PathBuf) -> Self {
        Self {
            inodes: Mutex::new(InodeTable::new(source.clone())),
            source,
        }
    }

    fn resolve(&self, ino: u64) -> Option<PathBuf> {
        self.inodes.lock().unwrap_or_else(|e| e.into_inner()).get_path(ino)
    }

    fn resolve_child(&self, parent: u64, name: &OsStr) -> Option<(u64, PathBuf)> {
        let mut table = self.inodes.lock().unwrap_or_else(|e| e.into_inner());
        let parent_path = table.get_path(parent)?;
        let child_path = parent_path.join(name);
        let ino = table.get_or_insert(child_path.clone());
        Some((ino, child_path))
    }
}

fn meta_to_attr(ino: u64, meta: &fs::Metadata) -> FileAttr {
    let kind = if meta.is_dir() {
        FileType::Directory
    } else if meta.is_symlink() {
        FileType::Symlink
    } else {
        FileType::RegularFile
    };
    let atime = meta.accessed().unwrap_or(UNIX_EPOCH);
    let mtime = meta.modified().unwrap_or(UNIX_EPOCH);
    let ctime = UNIX_EPOCH + Duration::from_secs(meta.ctime() as u64);
    FileAttr {
        ino: INodeNo(ino),
        size: meta.len(),
        blocks: meta.blocks(),
        atime,
        mtime,
        ctime,
        crtime: UNIX_EPOCH,
        kind,
        perm: meta.mode() as u16,
        nlink: meta.nlink() as u32,
        uid: meta.uid(),
        gid: meta.gid(),
        rdev: meta.rdev() as u32,
        blksize: meta.blksize() as u32,
        flags: 0,
    }
}

impl Filesystem for OstkFs {
    fn lookup(&self, _req: &Request, parent: INodeNo, name: &OsStr, reply: ReplyEntry) {
        let Some((ino, path)) = self.resolve_child(parent.0, name) else {
            reply.error(Errno::ENOENT);
            return;
        };
        match fs::symlink_metadata(&path) {
            Ok(meta) => reply.entry(&TTL, &meta_to_attr(ino, &meta), Generation(0)),
            Err(_) => reply.error(Errno::ENOENT),
        }
    }

    fn getattr(&self, _req: &Request, ino: INodeNo, _fh: Option<FileHandle>, reply: ReplyAttr) {
        let Some(path) = self.resolve(ino.0) else {
            reply.error(Errno::ENOENT);
            return;
        };
        match fs::symlink_metadata(&path) {
            Ok(meta) => reply.attr(&TTL, &meta_to_attr(ino.0, &meta)),
            Err(_) => reply.error(Errno::ENOENT),
        }
    }

    fn setattr(
        &self,
        _req: &Request,
        ino: INodeNo,
        mode: Option<u32>,
        _uid: Option<u32>,
        _gid: Option<u32>,
        size: Option<u64>,
        _atime: Option<fuser::TimeOrNow>,
        _mtime: Option<fuser::TimeOrNow>,
        _ctime: Option<SystemTime>,
        _fh: Option<FileHandle>,
        _crtime: Option<SystemTime>,
        _chgtime: Option<SystemTime>,
        _bkuptime: Option<SystemTime>,
        _flags: Option<BsdFileFlags>,
        reply: ReplyAttr,
    ) {
        let Some(path) = self.resolve(ino.0) else {
            reply.error(Errno::ENOENT);
            return;
        };
        // Handle truncate
        if let Some(new_size) = size {
            if let Ok(f) = OpenOptions::new().write(true).open(&path) {
                let _ = f.set_len(new_size);
            }
        }
        // Handle chmod
        if let Some(m) = mode {
            let _ = fs::set_permissions(&path, fs::Permissions::from_mode(m));
        }
        match fs::symlink_metadata(&path) {
            Ok(meta) => reply.attr(&TTL, &meta_to_attr(ino.0, &meta)),
            Err(_) => reply.error(Errno::ENOENT),
        }
    }

    fn readdir(
        &self,
        _req: &Request,
        ino: INodeNo,
        _fh: FileHandle,
        offset: u64,
        mut reply: ReplyDirectory,
    ) {
        let Some(path) = self.resolve(ino.0) else {
            reply.error(Errno::ENOENT);
            return;
        };
        let Ok(entries) = fs::read_dir(&path) else {
            reply.error(Errno::ENOENT);
            return;
        };

        let mut all: Vec<(u64, FileType, String)> = vec![
            (ino.0, FileType::Directory, ".".into()),
            (ino.0, FileType::Directory, "..".into()),
        ];

        let mut table = self.inodes.lock().unwrap_or_else(|e| e.into_inner());
        for entry in entries.flatten() {
            let name = entry.file_name().to_string_lossy().into_owned();
            let child_path = path.join(&name);
            let ft = if child_path.is_dir() {
                FileType::Directory
            } else {
                FileType::RegularFile
            };
            let child_ino = table.get_or_insert(child_path);
            all.push((child_ino, ft, name));
        }
        drop(table);

        for (i, (ino, kind, name)) in all.iter().enumerate().skip(offset as usize) {
            if reply.add(INodeNo(*ino), (i + 1) as u64, *kind, name) {
                break;
            }
        }
        reply.ok();
    }

    fn open(&self, _req: &Request, ino: INodeNo, _flags: fuser::OpenFlags, reply: ReplyOpen) {
        if self.resolve(ino.0).is_some() {
            reply.opened(FileHandle(0), FopenFlags::empty());
        } else {
            reply.error(Errno::ENOENT);
        }
    }

    fn read(
        &self,
        _req: &Request,
        ino: INodeNo,
        _fh: FileHandle,
        offset: u64,
        size: u32,
        _flags: fuser::OpenFlags,
        _lock_owner: Option<fuser::LockOwner>,
        reply: ReplyData,
    ) {
        let Some(path) = self.resolve(ino.0) else {
            reply.error(Errno::ENOENT);
            return;
        };
        let Ok(mut file) = fs::File::open(&path) else {
            reply.error(Errno::EIO);
            return;
        };
        if file.seek(SeekFrom::Start(offset)).is_err() {
            reply.error(Errno::EIO);
            return;
        }
        let mut buf = vec![0u8; size as usize];
        match file.read(&mut buf) {
            Ok(n) => reply.data(&buf[..n]),
            Err(_) => reply.error(Errno::EIO),
        }
    }

    fn write(
        &self,
        _req: &Request,
        ino: INodeNo,
        _fh: FileHandle,
        offset: u64,
        data: &[u8],
        _write_flags: WriteFlags,
        _flags: fuser::OpenFlags,
        _lock_owner: Option<fuser::LockOwner>,
        reply: ReplyWrite,
    ) {
        let Some(path) = self.resolve(ino.0) else {
            reply.error(Errno::ENOENT);
            return;
        };
        let Ok(mut file) = OpenOptions::new().write(true).open(&path) else {
            reply.error(Errno::EIO);
            return;
        };
        if file.seek(SeekFrom::Start(offset)).is_err() {
            reply.error(Errno::EIO);
            return;
        }
        match file.write(data) {
            Ok(n) => reply.written(n as u32),
            Err(_) => reply.error(Errno::EIO),
        }
    }

    fn create(
        &self,
        _req: &Request,
        parent: INodeNo,
        name: &OsStr,
        mode: u32,
        _umask: u32,
        _flags: i32,
        reply: ReplyCreate,
    ) {
        let Some((ino, path)) = self.resolve_child(parent.0, name) else {
            reply.error(Errno::ENOENT);
            return;
        };
        match fs::File::create(&path) {
            Ok(_) => {
                let _ = fs::set_permissions(&path, fs::Permissions::from_mode(mode));
                match fs::symlink_metadata(&path) {
                    Ok(meta) => {
                        reply.created(&TTL, &meta_to_attr(ino, &meta), Generation(0), FileHandle(0), FopenFlags::empty());
                    }
                    Err(_) => reply.error(Errno::EIO),
                }
            }
            Err(_) => reply.error(Errno::EIO),
        }
    }

    fn mkdir(
        &self,
        _req: &Request,
        parent: INodeNo,
        name: &OsStr,
        mode: u32,
        _umask: u32,
        reply: ReplyEntry,
    ) {
        let Some((ino, path)) = self.resolve_child(parent.0, name) else {
            reply.error(Errno::ENOENT);
            return;
        };
        match fs::create_dir(&path) {
            Ok(_) => {
                let _ = fs::set_permissions(&path, fs::Permissions::from_mode(mode));
                match fs::symlink_metadata(&path) {
                    Ok(meta) => reply.entry(&TTL, &meta_to_attr(ino, &meta), Generation(0)),
                    Err(_) => reply.error(Errno::EIO),
                }
            }
            Err(_) => reply.error(Errno::EIO),
        }
    }

    fn unlink(&self, _req: &Request, parent: INodeNo, name: &OsStr, reply: ReplyEmpty) {
        let Some((_ino, path)) = self.resolve_child(parent.0, name) else {
            reply.error(Errno::ENOENT);
            return;
        };
        match fs::remove_file(&path) {
            Ok(_) => {
                self.inodes.lock().unwrap_or_else(|e| e.into_inner()).remove(&path);
                reply.ok();
            }
            Err(_) => reply.error(Errno::EIO),
        }
    }

    fn rmdir(&self, _req: &Request, parent: INodeNo, name: &OsStr, reply: ReplyEmpty) {
        let Some((_ino, path)) = self.resolve_child(parent.0, name) else {
            reply.error(Errno::ENOENT);
            return;
        };
        match fs::remove_dir(&path) {
            Ok(_) => {
                self.inodes.lock().unwrap_or_else(|e| e.into_inner()).remove(&path);
                reply.ok();
            }
            Err(_) => reply.error(Errno::EIO),
        }
    }

    fn rename(
        &self,
        _req: &Request,
        parent: INodeNo,
        name: &OsStr,
        newparent: INodeNo,
        newname: &OsStr,
        _flags: RenameFlags,
        reply: ReplyEmpty,
    ) {
        let Some((_old_ino, old_path)) = self.resolve_child(parent.0, name) else {
            reply.error(Errno::ENOENT);
            return;
        };
        let Some((_new_ino, new_path)) = self.resolve_child(newparent.0, newname) else {
            reply.error(Errno::ENOENT);
            return;
        };
        match fs::rename(&old_path, &new_path) {
            Ok(_) => {
                self.inodes
                    .lock()
                    .unwrap_or_else(|e| e.into_inner())
                    .rename(&old_path, new_path);
                reply.ok();
            }
            Err(_) => reply.error(Errno::EIO),
        }
    }
}
