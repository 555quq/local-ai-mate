import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ImmersiveChat, type ImmersiveChatProps } from './ImmersiveChat'
import type { Conversation } from '../types'

const conversation: Conversation = {
  id: 'conversation-1',
  title: '和露娜聊聊',
  messages: [
    { id: 'm1', role: 'user', content: '今天过得有点累。', route: 'local', status: 'completed', created_at: '2026-10-08T09:00:00Z' },
    { id: 'm2', role: 'assistant', content: '辛苦了，先慢慢呼吸一下。', route: 'local', status: 'completed', created_at: '2026-10-08T09:00:01Z' },
  ],
}

afterEach(cleanup)

function renderChat(overrides: Partial<ImmersiveChatProps> = {}) {
  const props: ImmersiveChatProps = {
    conversation,
    draft: '',
    route: 'local',
    sending: false,
    health: null,
    voiceActive: false,
    voicePhase: 'idle',
    autoRead: true,
    activeMessageId: null,
    loadingMessageId: null,
    onDraftChange: vi.fn(),
    onRouteChange: vi.fn(),
    onSubmit: vi.fn(),
    onNavigate: vi.fn(),
    onStartVoice: vi.fn(),
    onStopVoice: vi.fn(),
    onAutoReadChange: vi.fn(),
    onPlayMessage: vi.fn(),
    ...overrides,
  }
  return { props, ...render(<ImmersiveChat {...props} />) }
}

describe('ImmersiveChat', () => {
  it('keeps history hidden until its button is used', () => {
    renderChat()
    expect(screen.queryByRole('dialog', { name: '对话历史' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '打开对话历史' }))
    expect(screen.getByRole('dialog', { name: '对话历史' })).toBeTruthy()
    expect(within(screen.getByRole('dialog', { name: '对话历史' })).getByText('今天过得有点累。')).toBeTruthy()
  })

  it('closes history with Escape and restores focus to the trigger', () => {
    renderChat()
    const trigger = screen.getByRole('button', { name: '打开对话历史' })
    fireEvent.click(trigger)
    expect(document.activeElement).toBe(screen.getByRole('button', { name: '关闭对话历史' }))
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(screen.queryByRole('dialog', { name: '对话历史' })).toBeNull()
    expect(document.activeElement).toBe(trigger)
  })

  it('closes history from the blank backdrop and restores focus', () => {
    renderChat()
    const trigger = screen.getByRole('button', { name: '打开对话历史' })
    fireEvent.click(trigger)
    fireEvent.mouseDown(screen.getByTestId('history-backdrop'))
    expect(screen.queryByRole('dialog', { name: '对话历史' })).toBeNull()
    expect(document.activeElement).toBe(trigger)
  })

  it('builds the S1 subtitles from the latest user and assistant messages', () => {
    renderChat()
    const subtitles = screen.getByLabelText('当前字幕')
    expect(subtitles.textContent).toContain('你今天过得有点累。')
    expect(subtitles.textContent).toContain('露娜辛苦了，先慢慢呼吸一下。')
  })

  it('shows the stop action only while a V3 voice session is active', () => {
    const { rerender, props } = renderChat()
    expect(screen.queryByRole('button', { name: '停止连续语音会话' })).toBeNull()
    rerender(<ImmersiveChat {...props} voiceActive voicePhase="listening" />)
    expect(screen.getByRole('button', { name: '停止连续语音会话' })).toBeTruthy()
  })

  it('gives every icon-only control a Chinese accessible name', () => {
    renderChat()
    expect(screen.getByRole('button', { name: '打开对话历史' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '打开今日' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '打开记忆' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '打开设置' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '开始连续语音会话' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '发送消息' })).toBeTruthy()
  })
})
