import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import { DEFAULT_RELATIONSHIP_SETTINGS, type RelationshipSettings } from '../types'
import { RelationshipSettingsPage } from './RelationshipSettings'

const stored: RelationshipSettings = {
  ...DEFAULT_RELATIONSHIP_SETTINGS,
  version: 4,
  updated_at: '2026-10-09T08:00:00Z',
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('RelationshipSettingsPage', () => {
  it('previews and saves only changed structured fields', async () => {
    const onSaved = vi.fn()
    const saved = { ...stored, companion_name: '月影', personality_style: 'cool-restrained' as const, version: 5 }
    const update = vi.spyOn(api, 'updateRelationshipSettings').mockResolvedValue(saved)
    render(<RelationshipSettingsPage settings={stored} onSaved={onSaved} />)

    fireEvent.change(screen.getByLabelText('伴侣名字'), { target: { value: '月影' } })
    fireEvent.change(screen.getByLabelText('性格风格'), { target: { value: 'cool-restrained' } })
    expect(screen.getByText(/月影：你，我会保持清醒/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '保存设置' }))

    await waitFor(() => expect(update).toHaveBeenCalledWith(
      { companion_name: '月影', personality_style: 'cool-restrained' },
      4,
    ))
    expect(onSaved).toHaveBeenCalledWith(saved)
  })

  it('keeps draft values when a save conflicts', async () => {
    vi.spyOn(api, 'updateRelationshipSettings').mockRejectedValue(new Error('conflict'))
    render(<RelationshipSettingsPage settings={stored} onSaved={vi.fn()} />)
    const name = screen.getByLabelText('伴侣名字') as HTMLInputElement
    fireEvent.change(name, { target: { value: '星夜' } })
    fireEvent.click(screen.getByRole('button', { name: '保存设置' }))
    await waitFor(() => expect(screen.getByRole('status').textContent).toContain('没有保存'))
    expect(name.value).toBe('星夜')
  })

  it('resets only after page confirmation', async () => {
    const reset = vi.spyOn(api, 'resetRelationshipSettings').mockResolvedValue({ ...stored, version: 5 })
    render(<RelationshipSettingsPage settings={stored} onSaved={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: '恢复默认' }))
    expect(reset).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '确认恢复' }))
    await waitFor(() => expect(reset).toHaveBeenCalledWith(4))
  })
})
