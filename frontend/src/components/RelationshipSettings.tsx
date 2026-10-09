import { useEffect, useMemo, useState } from 'react'
import { ApiError, api } from '../api/client'
import type { RelationshipSettings } from '../types'

interface Props {
  settings: RelationshipSettings
  onSaved: (settings: RelationshipSettings) => void
}

type EditableSettings = Omit<RelationshipSettings, 'version' | 'updated_at'>

const editableKeys: Array<keyof EditableSettings> = [
  'companion_name', 'self_reference', 'user_display_name', 'user_nickname', 'nickname_frequency',
  'personality_style', 'intimacy_level', 'reply_length', 'question_frequency', 'comfort_style',
  'opening_greeting', 'blocked_terms',
]

function editable(settings: RelationshipSettings): EditableSettings {
  return Object.fromEntries(editableKeys.map((key) => [key, settings[key]])) as EditableSettings
}

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : '关系设定没有保存，请稍后重试。'
}

export function RelationshipSettingsPage({ settings, onSaved }: Props) {
  const [draft, setDraft] = useState<EditableSettings>(() => editable(settings))
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [resetOpen, setResetOpen] = useState(false)

  useEffect(() => { setDraft(editable(settings)) }, [settings])

  const changed = useMemo(
    () => JSON.stringify(draft) !== JSON.stringify(editable(settings)),
    [draft, settings],
  )

  const update = <K extends keyof EditableSettings>(key: K, value: EditableSettings[K]) => {
    setDraft((current) => ({ ...current, [key]: value }))
    setNotice(null)
  }

  const save = async () => {
    if (!changed || saving) return
    const patch: Partial<EditableSettings> = {}
    const original = editable(settings)
    for (const key of editableKeys) {
      if (JSON.stringify(draft[key]) !== JSON.stringify(original[key])) {
        Object.assign(patch, { [key]: draft[key] })
      }
    }
    setSaving(true)
    setNotice(null)
    try {
      const saved = await api.updateRelationshipSettings(patch, settings.version)
      onSaved(saved)
      setNotice('关系设定已保存到本机，并会从下一条回复开始生效。')
    } catch (error) {
      setNotice(errorMessage(error))
    } finally {
      setSaving(false)
    }
  }

  const reset = async () => {
    if (saving) return
    setSaving(true)
    setNotice(null)
    try {
      const saved = await api.resetRelationshipSettings(settings.version)
      onSaved(saved)
      setResetOpen(false)
      setNotice('已恢复默认关系设定。')
    } catch (error) {
      setNotice(errorMessage(error))
    } finally {
      setSaving(false)
    }
  }

  const nickname = draft.user_nickname || draft.user_display_name || '你'
  const preview = `${draft.companion_name || '露娜'}：${nickname}，${previewStyle(draft.personality_style)}。${previewComfort(draft.comfort_style)}`

  return <section className="page-content relationship-page">
    <div className="page-heading"><div><p className="eyebrow">仅保存在本机</p><h1>关系设定</h1><p className="hint">设置名字、称呼与回答方式，不会训练模型，也不会改写历史对话。</p></div></div>
    {notice && <p className={notice.includes('已') || notice.includes('生效') ? 'notice' : 'error'} role="status">{notice}</p>}

    <div className="relationship-grid">
      <section className="settings-group"><h2>身份与称呼</h2><div className="field-grid">
        <TextField label="伴侣名字" value={draft.companion_name} maxLength={20} required onChange={(value) => update('companion_name', value)} />
        <TextField label="伴侣自称" value={draft.self_reference} maxLength={12} required onChange={(value) => update('self_reference', value)} />
        <TextField label="你的名字（可选）" value={draft.user_display_name} maxLength={20} onChange={(value) => update('user_display_name', value)} />
        <TextField label="希望她怎么称呼你" value={draft.user_nickname} maxLength={20} onChange={(value) => update('user_nickname', value)} />
        <SelectField label="昵称使用频率" value={draft.nickname_frequency} onChange={(value) => update('nickname_frequency', value as EditableSettings['nickname_frequency'])} options={[
          ['never', '不使用'], ['rare', '很少'], ['natural', '自然'], ['frequent', '较常'],
        ]} />
      </div></section>

      <section className="settings-group"><h2>回答方式</h2><div className="field-grid">
        <SelectField label="性格风格" value={draft.personality_style} onChange={(value) => update('personality_style', value as EditableSettings['personality_style'])} options={[
          ['gentle-intellectual', '温柔知性'], ['cool-restrained', '清冷克制'], ['bright-playful', '明快活泼'], ['calm-supportive', '沉稳陪伴'],
        ]} />
        <SelectField label="亲密表达" value={draft.intimacy_level} onChange={(value) => update('intimacy_level', value as EditableSettings['intimacy_level'])} options={[
          ['restrained', '克制'], ['natural', '自然'], ['close', '亲近'],
        ]} />
        <SelectField label="回答长度" value={draft.reply_length} onChange={(value) => update('reply_length', value as EditableSettings['reply_length'])} options={[
          ['short', '简短'], ['medium', '适中'], ['detailed', '详细'],
        ]} />
        <SelectField label="主动提问" value={draft.question_frequency} onChange={(value) => update('question_frequency', value as EditableSettings['question_frequency'])} options={[
          ['low', '较少'], ['medium', '适中'], ['high', '较多'],
        ]} />
        <SelectField label="安慰方式" value={draft.comfort_style} onChange={(value) => update('comfort_style', value as EditableSettings['comfort_style'])} options={[
          ['listen-first', '先倾听'], ['balanced', '倾听与建议平衡'], ['advice-first', '先给建议'],
        ]} />
      </div></section>

      <section className="settings-group"><h2>表达边界</h2><div className="field-grid single">
        <TextField label="开场问候（可选）" value={draft.opening_greeting} maxLength={120} onChange={(value) => update('opening_greeting', value)} />
        <label className="relationship-field"><span>禁用称呼（最多 8 个，用逗号分隔）</span><textarea aria-label="禁用称呼" rows={3} value={draft.blocked_terms.join('，')} onChange={(event) => update('blocked_terms', parseBlockedTerms(event.target.value))} placeholder="例如：宝宝，主人" /><small>{draft.blocked_terms.length}/8</small></label>
      </div></section>

      <section className="settings-group relationship-preview"><h2>即时预览</h2><p>{preview}</p><small>预览由本地模板生成，不调用模型或语音服务。</small></section>
    </div>

    <div className="relationship-actions">
      <div><strong>{changed ? '有未保存修改' : '当前设置已同步'}</strong>{settings.updated_at && <small>上次保存：{new Date(settings.updated_at).toLocaleString()}</small>}</div>
      <button className="secondary" type="button" disabled={saving} onClick={() => setResetOpen(true)}>恢复默认</button>
      <button type="button" disabled={saving || !changed || !draft.companion_name.trim() || !draft.self_reference.trim()} onClick={() => void save()}>{saving ? '正在保存…' : '保存设置'}</button>
    </div>
    {resetOpen && <div className="confirm-box"><h3>恢复默认关系设定？</h3><p>名字、昵称和回答方式会恢复为初始值；历史消息不会改变。</p><div className="inline-actions"><button className="secondary" type="button" onClick={() => setResetOpen(false)}>取消</button><button className="danger" type="button" disabled={saving} onClick={() => void reset()}>确认恢复</button></div></div>}
  </section>
}

function TextField({ label, value, maxLength, required = false, onChange }: { label: string; value: string; maxLength: number; required?: boolean; onChange: (value: string) => void }) {
  return <label className="relationship-field"><span>{label}</span><input aria-label={label} value={value} maxLength={maxLength} required={required} onChange={(event) => onChange(event.target.value)} /><small>{value.length}/{maxLength}</small></label>
}

function SelectField({ label, value, options, onChange }: { label: string; value: string; options: string[][]; onChange: (value: string) => void }) {
  return <label className="relationship-field"><span>{label}</span><select aria-label={label} value={value} onChange={(event) => onChange(event.target.value)}>{options.map(([key, text]) => <option key={key} value={key}>{text}</option>)}</select></label>
}

function parseBlockedTerms(value: string): string[] {
  return value.split(/[，,\n]/).map((item) => item.trim().slice(0, 20)).filter(Boolean).filter((item, index, all) => all.indexOf(item) === index).slice(0, 8)
}

function previewStyle(value: RelationshipSettings['personality_style']): string {
  return ({ 'gentle-intellectual': '我会温柔、认真地听你说', 'cool-restrained': '我会保持清醒，也会在这里陪你', 'bright-playful': '今天也可以轻松一点聊', 'calm-supportive': '我们可以慢慢把事情理清楚' })[value]
}

function previewComfort(value: RelationshipSettings['comfort_style']): string {
  return ({ 'listen-first': '你可以先说，我在听。', balanced: '我会先听，再陪你找一个可行的小步骤。', 'advice-first': '如果你愿意，我们先从最可控的一步开始。' })[value]
}
