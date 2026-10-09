import { useEffect, useState } from 'react'
import { ApiError, api } from '../api/client'
import type { PrivacySettings } from '../types'

const defaults: PrivacySettings = { memory_candidates_paused: false, retrieval_paused: false, proactive_interactions_paused: false }
const errorMessage = (error: unknown) => error instanceof ApiError ? error.message : '本地隐私操作未完成；没有数据被上传或删除。'

export function PrivacyControls() {
  const [settings, setSettings] = useState<PrivacySettings>(defaults)
  const [capable, setCapable] = useState<boolean | null>(null)
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [clearOpen, setClearOpen] = useState(false)
  const [clearText, setClearText] = useState('')
  const [clearAcknowledged, setClearAcknowledged] = useState(false)

  useEffect(() => { void (async () => { try { const stored = await api.getPrivacySettings(); setCapable(Boolean(stored)); if (stored) setSettings(stored) } catch (error) { setCapable(false); setNotice(errorMessage(error)) } })() }, [])
  const save = async (patch: Partial<PrivacySettings>) => {
    const previous = settings; const next = { ...settings, ...patch }; setSettings(next); setSaving(true); setNotice(null)
    try { const saved = await api.updatePrivacySettings(patch); if (!saved) { setSettings(previous); setCapable(false); setNotice('此版本后端尚未开放持久化设置；开关没有保存。'); return }; setSettings(saved); setCapable(true); setNotice('设置已保存到本机。') } catch (error) { setSettings(previous); setNotice(errorMessage(error)) } finally { setSaving(false) }
  }
  const exportData = async () => {
    setSaving(true); setNotice(null)
    try { const exported = await api.exportPrivacyData(); if (exported === undefined) { setNotice('此版本后端尚未开放本地导出；未生成任何文件。'); return }; const blob = new Blob([JSON.stringify(exported, null, 2)], { type: 'application/json' }); const url = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = url; link.download = `qiyu-local-export-${new Date().toISOString().slice(0, 10)}.json`; link.click(); URL.revokeObjectURL(url); setNotice('本地导出已开始下载。该操作不会上传数据。') } catch (error) { setNotice(errorMessage(error)) } finally { setSaving(false) }
  }
  const clearData = async () => {
    if (clearText !== '清空本地数据' || !clearAcknowledged) return
    setSaving(true); setNotice(null)
    try { const result = await api.clearPrivacyData(); if (result === undefined) { setNotice('此版本后端尚未开放清空接口；没有删除任何数据。'); return }; setClearOpen(false); setClearText(''); setClearAcknowledged(false); setSettings(defaults); setNotice('本地数据已清空。请刷新对话和今日页面以查看空状态。') } catch (error) { setNotice(errorMessage(error)) } finally { setSaving(false) }
  }
  const disabled = saving || capable === false
  return <section className="page-content narrow">
    <div className="page-heading"><div><p className="eyebrow">本地优先</p><h1>隐私与云端</h1></div></div>
    {notice && <p className={notice.includes('已') || notice.includes('开始') ? 'notice' : 'error'} role="status">{notice}</p>}
    {capable === null && <p className="hint">正在读取本机设置…</p>}{capable === false && <p className="notice">本地后端尚未提供部分持久化隐私接口。界面不会把临时选择伪装成已保存。</p>}
    <section className="settings-group"><h2>云端增强</h2><p className="hint">云端出站默认关闭。即使以后开放，每次云端增强仍必须显示上传范围，并由本地后端签发一次性授权。浏览器不会直接连接云端。</p></section>
    <section className="settings-group"><h2>本地记忆与互动</h2><Toggle label="暂停收集记忆候选" checked={settings.memory_candidates_paused} disabled={disabled} onChange={(value) => void save({ memory_candidates_paused: value })} /><Toggle label="暂停本地记忆检索" checked={settings.retrieval_paused} disabled={disabled} onChange={(value) => void save({ retrieval_paused: value })} /><Toggle label="暂停主动互动" checked={settings.proactive_interactions_paused} disabled={disabled} onChange={(value) => void save({ proactive_interactions_paused: value })} /></section>
    <section className="settings-group danger-zone"><h2>备份与删除</h2><p>导出会从本地后端取得数据并下载到此设备；不会上传。清空会永久删除本地保存的对话、记忆和提醒，不能撤销。</p><div className="inline-actions"><button className="secondary" onClick={() => void exportData()} disabled={saving || capable === false}>导出本地数据</button><button className="danger" onClick={() => setClearOpen(true)} disabled={saving || capable === false}>清空本地数据</button></div>{clearOpen && <div className="confirm-box"><h3>确认清空</h3><p>请输入“清空本地数据”并勾选确认，才能发送本地删除请求。</p><input value={clearText} onChange={(event) => setClearText(event.target.value)} placeholder="清空本地数据" aria-label="确认清空文字" /><label className="checkbox"><input type="checkbox" checked={clearAcknowledged} onChange={(event) => setClearAcknowledged(event.target.checked)} />我理解此操作不可撤销。</label><div className="inline-actions"><button className="secondary" onClick={() => setClearOpen(false)}>取消</button><button className="danger" onClick={() => void clearData()} disabled={saving || clearText !== '清空本地数据' || !clearAcknowledged}>永久清空</button></div></div>}</section>
  </section>
}

function Toggle({ label, checked, disabled, onChange }: { label: string; checked: boolean; disabled: boolean; onChange: (value: boolean) => void }) { return <label className="toggle"><input type="checkbox" checked={checked} disabled={disabled} onChange={(event) => onChange(event.target.checked)} /><span aria-hidden="true" />{label}</label> }
