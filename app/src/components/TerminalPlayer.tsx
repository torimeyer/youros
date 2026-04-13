/**
 * TerminalPlayer - plays back an asciicast v2 recording.
 *
 * Loads asciinema-player from CDN the first time it is mounted, then
 * calls the player API to render inside a container div.
 */
import { useEffect, useRef, useState } from "react";

interface Props {
  castUrl: string;
  width?: number;
  height?: number;
}

// Track whether the CDN assets have already been injected.
let playerLoaded = false;
let playerLoading = false;
const loadCallbacks: Array<() => void> = [];

function loadPlayer(onReady: () => void) {
  if (playerLoaded) {
    onReady();
    return;
  }
  loadCallbacks.push(onReady);
  if (playerLoading) return;
  playerLoading = true;

  // asciinema-player v3 standalone bundle
  const cssHref = "https://cdn.jsdelivr.net/npm/asciinema-player@3/dist/bundle/asciinema-player.css";
  const scriptSrc = "https://cdn.jsdelivr.net/npm/asciinema-player@3/dist/bundle/asciinema-player.min.js";

  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = cssHref;
  document.head.appendChild(link);

  const script = document.createElement("script");
  script.src = scriptSrc;
  script.onload = () => {
    playerLoaded = true;
    playerLoading = false;
    loadCallbacks.splice(0).forEach((cb) => cb());
  };
  document.head.appendChild(script);
}

declare global {
  interface Window {
    AsciinemaPlayer?: {
      create: (
        src: string,
        el: HTMLElement,
        opts?: Record<string, unknown>
      ) => { dispose: () => void };
    };
  }
}

export default function TerminalPlayer({ castUrl, width = 80, height = 24 }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [ready, setReady] = useState(playerLoaded);

  useEffect(() => {
    loadPlayer(() => setReady(true));
  }, []);

  useEffect(() => {
    if (!ready || !containerRef.current || !window.AsciinemaPlayer) return;

    const player = window.AsciinemaPlayer.create(castUrl, containerRef.current, {
      cols: width,
      rows: height,
      autoPlay: false,
      fit: "width",
    });

    return () => {
      player.dispose();
    };
  }, [ready, castUrl, width, height]);

  if (!ready) {
    return (
      <div className="flex items-center justify-center h-32 text-slate-500 text-sm">
        Loading player...
      </div>
    );
  }

  return <div ref={containerRef} className="w-full rounded overflow-hidden" />;
}
