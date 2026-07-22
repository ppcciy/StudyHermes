import { useEffect, useRef } from 'react'

const MIDNIGHT_REFRESH_DELAY_MS = 5_000

function millisecondsUntilNextMidnight(): number {
  const now = new Date()
  const next = new Date(now)

  next.setHours(24, 0, Math.floor(MIDNIGHT_REFRESH_DELAY_MS / 1000), 0)

  return Math.max(1_000, next.getTime() - now.getTime())
}

export function useMidnightRefresh(refresh: () => void): void {
  const refreshRef = useRef(refresh)

  useEffect(() => {
    refreshRef.current = refresh
  }, [refresh])

  useEffect(() => {
    let timerId = 0

    const schedule = () => {
      timerId = window.setTimeout(() => {
        refreshRef.current()
        schedule()
      }, millisecondsUntilNextMidnight())
    }

    schedule()

    return () => window.clearTimeout(timerId)
  }, [])
}
