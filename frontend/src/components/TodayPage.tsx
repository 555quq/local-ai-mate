import { useEffect, useMemo, useState } from 'react'
import { ApiError, api } from '../api/client'
import { ReminderProposalCard } from './ReminderProposalCard'
import type { Reminder, ReminderProposal } from '../types'

function errorMessage(error: unknown) { return error instanceof ApiError ? error.message : '本地提醒暂时不可用，未创建任何提醒。' }

function displayTime(reminder: Reminder) {
  try {
    return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short', timeZone: reminder.timezone }).format(new Date(reminder.trigger_at))
  } catch { return reminder.trigger_at }
}

export function TodayPage({ conversationId }: { conversationId?: string }) {
  const [reminders, setReminders] = useState<Reminder[]>([])
  const [proposal, setProposal] = useState<ReminderProposal | null>(null)
  const [title, setTitle] = useState('')
  const [dueAt, setDueAt] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const timezone = useMemo(() => Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Shanghai', [])

  const refresh = async () => {
    setLoading(true)
    try { setReminders(await api.listReminders()) } catch (error) { setNotice(errorMessage(error)) } finally { setLoading(false) }
  }
  useEffect(() => { void refresh() }, [])

  const propose = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!title.trim() || !dueAt) return
    const date = new Date(dueAt)
    if (Number.isNaN(date.valueOf())) { setNotice('请选择有效的日期和时间。'); return }
    setSaving(true); setNotice(null)
    try {
      setProposal(await api.createReminderProposal({ conversation_id: conversationId, title: title.trim(), absolute_due_at: date.toISOString(), timezone, source_text: `手动创建：${title.trim()}` }))
    } catch (error) { setNotice(errorMessage(error)) } finally { setSaving(false) }
  }

  const decide = async (resolution: 'create' | 'replace' | 'cancel') => {
    if (resolution === 'cancel') { setProposal(null); return }
    if (!proposal) return
    setSaving(true); setNotice(null)
    try {
      await api.confirmReminder(proposal.id, resolution === 'replace')
      setProposal(null); setTitle(''); setDueAt('')
      await refresh()
      setNotice('提醒已保存在本机。')
    } catch (error) { setNotice(errorMessage(error)) } finally { setSaving(false) }
  }

  return <section className="page-content">
    <div className="page-heading"><div><p className="eyebrow">本地日程</p><h1>今天</h1></div><p>提醒只会在你确认后写入本地数据库。</p></div>
    {notice && <p className={notice === '提醒已保存在本机。' ? 'notice' : 'error'} role="status">{notice}</p>}
    <div className="today-grid">
      <section className="settings-group"><h2>创建本地提醒</h2><form className="reminder-form" onSubmit={(event) => void propose(event)}>
        <label>事项<input value={title} maxLength={200} onChange={(event) => setTitle(event.target.value)} placeholder="例如：缴纳水电费" required /></label>
        <label>时间<input type="datetime-local" value={dueAt} onChange={(event) => setDueAt(event.target.value)} required /></label>
        <p className="hint">时区：{timezone}。提交后仍需确认，才会创建。</p>
        <button disabled={saving || !title.trim() || !dueAt}>{saving ? '正在保存…' : '生成确认卡'}</button>
      </form>
      {proposal && <ReminderProposalCard proposal={proposal} onDecision={(resolution) => void decide(resolution)} />}
      </section>
      <section className="settings-group"><div className="section-title"><h2>已确认的提醒</h2><button className="secondary compact" onClick={() => void refresh()} disabled={loading}>刷新</button></div>
        {loading ? <p className="hint">正在从本机加载…</p> : reminders.length === 0 ? <p className="hint">还没有已确认的提醒。</p> : <ul className="reminder-list">{reminders.map((reminder) => <li key={reminder.id}><strong>{reminder.title}</strong><span>{displayTime(reminder)}（{reminder.timezone}）{reminder.repeat_rule ? ` · ${reminder.repeat_rule}` : ''}</span></li>)}</ul>}
      </section>
    </div>
  </section>
}
