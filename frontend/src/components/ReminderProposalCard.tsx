import type { ReminderProposal } from '../types'

export function ReminderProposalCard({ proposal, onDecision }: { proposal: ReminderProposal; onDecision: (resolution: 'create' | 'replace' | 'cancel') => void }) {
  const time = new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short', timeZone: proposal.timezone }).format(new Date(proposal.absolute_due_at))
  return <article className="proposal-card">
    <p className="eyebrow">待确认的本地提醒</p>
    <h3>{proposal.title}</h3>
    <p>{time}（{proposal.timezone}）{proposal.repeat_rule ? ` · ${proposal.repeat_rule}` : ''}</p>
    <div className="inline-actions">
      <button className="secondary" onClick={() => onDecision('cancel')}>稍后处理</button>
      <button className="secondary" onClick={() => onDecision('replace')}>如重复则替换</button>
      <button onClick={() => onDecision('create')}>确认创建</button>
    </div>
  </article>
}
