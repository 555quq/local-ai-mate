import { describe, expect, it } from 'vitest'
import { OFFLINE_MESSAGE } from './App'

describe('public deployment offline copy', () => {
  it('explains that the private AI host is offline without exposing loopback instructions', () => {
    expect(OFFLINE_MESSAGE).toContain('本地 AI 主机暂时离线')
    expect(OFFLINE_MESSAGE).not.toContain('127.0.0.1')
  })
})
