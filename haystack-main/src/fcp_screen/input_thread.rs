//! Dedicated input thread — captures keystrokes independently of render cycle.

use std::sync::mpsc::{self, Receiver, Sender};
use std::thread;
use std::time::Duration;
use crossterm::event::{self, Event};

#[derive(Debug)]
pub enum InputEvent {
    Terminal(Event),
}

pub fn spawn() -> (Sender<()>, Receiver<InputEvent>) {
    let (tx, rx) = mpsc::channel();
    let (kill_tx, kill_rx) = mpsc::channel::<()>();

    thread::spawn(move || {
        loop {
            match event::poll(Duration::from_millis(1)) {
                Ok(true) => {
                    if let Ok(e) = event::read()
                        && tx.send(InputEvent::Terminal(e)).is_err()
                    {
                        break;
                    }
                }
                Ok(false) => {
                    if kill_rx.try_recv().is_ok() { break; }
                }
                Err(_) => break,
            }
        }
    });

    (kill_tx, rx)
}

/// Spawn an input thread that sends events directly into a unified `AppMessage` channel.
pub(crate) fn spawn_into(app_tx: Sender<super::app::AppMessage>) {
    thread::Builder::new()
        .name("input".into())
        .spawn(move || {
            loop {
                match event::poll(Duration::from_millis(1)) {
                    Ok(true) => {
                        if let Ok(e) = event::read()
                            && app_tx.send(super::app::AppMessage::Input(e)).is_err()
                        {
                            break;
                        }
                    }
                    Ok(false) => {
                        // Yield briefly to avoid busy-spin
                        std::thread::sleep(Duration::from_millis(1));
                    }
                    Err(_) => break,
                }
            }
        })
        .ok();
}
