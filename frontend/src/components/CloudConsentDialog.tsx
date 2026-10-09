import type { CloudPreview } from '../types'

interface Props {
  preview: CloudPreview | null
  loading: boolean
  error: string | null
  onApprove: () => void
  onCancel: () => void
}

export function CloudConsentDialog({ preview, loading, error, onApprove, onCancel }: Props) {
  return <div className="overlay" role="presentation">
    <section className="dialog" role="dialog" aria-modal="true" aria-labelledby="cloud-consent-title">
      <p className="eyebrow">需要你的明确允许</p>
      <h2 id="cloud-consent-title">确认本次云端增强</h2>
      {loading && <p>正在生成本地上传预览…</p>}
      {error && <p className="error" role="alert">{error}</p>}
      {preview && <>
        <dl className="preview-details">
          <div><dt>目标服务</dt><dd>{preview.provider_id}</dd></div>
          <div><dt>上传模式</dt><dd>{preview.mode === 'cloud-current-message' ? '仅当前消息' : '当前消息 + 临时摘要'}</dd></div>
          {preview.fields.map((field) => <div key={field.name}><dt>{field.name}</dt><dd>{field.character_count} 个字符</dd></div>)}
        </dl>
        <p className="hint">完整记录、长期记忆、音频和待办不会上传。MVP 只验证本次授权，实际回答仍由本地模型生成；授权仅限本次发送。</p>
      </>}
      <div className="dialog-actions">
        <button className="secondary" autoFocus onClick={onCancel}>取消</button>
        <button disabled={!preview || loading} onClick={onApprove}>仅本次允许并发送</button>
      </div>
    </section>
  </div>
}
