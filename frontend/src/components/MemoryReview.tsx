import { useEffect, useState } from 'react'
import { ApiError, api } from '../api/client'
import type { MemoryCandidate } from '../types'

export function MemoryReview({ conversationId }: { conversationId: string | undefined }) {
  const [items, setItems] = useState<MemoryCandidate[]>([])
  const [notice, setNotice] = useState<string | null>(null)
  useEffect(() => { if (conversationId) void api.listMemoryCandidates(conversationId).then(setItems).catch((error: unknown) => setNotice(error instanceof ApiError ? error.message : '候选记忆暂时无法加载。')) }, [conversationId])
  const decide = async (item: MemoryCandidate, privateLocalOnly: boolean) => {
    try {
      await api.approveMemoryCandidate(item, privateLocalOnly)
      setItems((current) => current.filter((candidate) => candidate.id !== item.id))
    } catch (error) { setNotice(error instanceof ApiError ? error.message : '操作未保存。') }
  }
  return <section className="page-content">
    <div className="page-heading"><div><p className="eyebrow">仅在本机保存</p><h1>记忆审核</h1></div><p>候选不会参与对话，直到你亲自确认。</p></div>
    {notice && <p className="error" role="alert">{notice}</p>}
    {items.length === 0 ? <div className="empty-state"><h2>没有待审核的记忆</h2><p>稳定偏好和明确计划才会出现在这里。</p></div> : <div className="cards">
      {items.map((item) => <article className="memory-card" key={item.id}>
        <span className="tag">{item.category}</span><h2>{item.content}</h2><blockquote>来源消息 ID：{item.source_message_id}</blockquote>
        <p className="hint">候选不参与检索，直到你批准。MVP 不提供忽略接口，未批准项目会继续留在这里。</p>
        <div className="inline-actions"><button className="secondary" onClick={() => void decide(item, true)}>仅本地保存</button><button onClick={() => void decide(item, false)}>批准</button></div>
      </article>)}
    </div>}
  </section>
}
