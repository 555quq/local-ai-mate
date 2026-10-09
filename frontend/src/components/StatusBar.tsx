import type { HealthState, OllamaStatus, Route } from '../types'

interface Props {
  route: Route
  health: HealthState | null
}

const routeLabel: Record<Route, string> = {
  local: '本地 · 均衡模式',
  'cloud-current-message': '云端增强 · 仅当前消息',
  'cloud-with-summary': '云端增强 · 临时摘要',
}

function ollama(health: HealthState | null): OllamaStatus | null {
  return health && typeof health.model !== 'string' ? health.model : null
}

function modelLabel(health: HealthState | null, route: Route) {
  if (!health) return '本地服务未连接'
  const state = ollama(health)
  if (state?.status === 'ready') return `${routeLabel[route]} · Ollama：${state.model ?? '已就绪'}`
  if (state?.status === 'disabled') return '本地回退模式 · Ollama 未启用'
  if (state) return `本地回退模式 · Ollama ${state.status}${state.reason ? `（${state.reason}）` : ''}`
  if (typeof health.model === 'string') {
    if (health.model === 'mock-local-ready') return '本地模型模拟模式 · 未连接 Ollama'
    if (health.model.includes('fallback')) return '本地回退模式 · Ollama 不可用'
    return `本地模型：${health.model}`
  }
  return '本地回退模式 · 模型状态未知'
}

export function StatusBar({ route, health }: Props) {
  const state = ollama(health)
  const modelReady = Boolean(state?.status === 'ready' || (typeof health?.model === 'string' && (health.model === 'ollama-ready' || health.model.startsWith('ollama-'))))
  const fallback = Boolean(health && !modelReady)
  const ttsStatus = health?.tts?.status
  const ttsAvailable = ttsStatus === 'configured' || ttsStatus === 'ready' || ttsStatus === 'warming' || ttsStatus === 'degraded'
  const ttsLabel = ttsStatus === 'ready' ? '露娜语音已就绪' : ttsStatus === 'warming' ? '露娜语音预热中' : ttsStatus === 'degraded' ? '语音回退可用' : ttsStatus === 'configured' ? '本地语音已配置' : '本地语音不可用'
  return <header className="status-bar" aria-label="应用状态">
    <span className="model-status"><i className={modelReady ? 'dot good' : fallback ? 'dot warning' : 'dot neutral'} />{modelLabel(health, route)}</span>
    <span><i className={health?.database === 'ready' ? 'dot good' : 'dot warning'} />{health?.database === 'ready' ? '本地数据库正常' : '数据库状态未知'}</span>
    <span><i className={ttsAvailable ? 'dot good' : 'dot warning'} />{ttsLabel}</span>
    <span><i className={health?.cloud_outbound_enabled ? 'dot warning' : 'dot good'} />{health?.cloud_outbound_enabled ? '云端出站已开启' : '云端出站默认关闭'}</span>
  </header>
}
