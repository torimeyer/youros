import { create } from 'zustand'

interface CalendarEvent {
  id: string
  summary?: string
  start: { dateTime?: string; date?: string }
  end: { dateTime?: string; date?: string }
  location?: string
  htmlLink?: string
  hangoutLink?: string
  colorId?: string
}

interface CalendarState {
  events: CalendarEvent[]
  wsConnected: boolean
  setEvents: (events: CalendarEvent[]) => void
  setWsConnected: (connected: boolean) => void
}

export const useCalendarStore = create<CalendarState>((set) => ({
  events: [],
  wsConnected: false,
  setEvents: (events) => set({ events }),
  setWsConnected: (connected) => set({ wsConnected: connected }),
}))
