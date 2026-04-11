<template>
  <div class="monitor-container">
    <!-- Header -->
    <nav class="navbar">
      <div class="nav-brand" @click="router.push('/')">MIROFISH</div>
      <div class="nav-title">Simulation Monitor</div>
      <div class="nav-actions">
        <button class="refresh-btn" @click="fetchSimulations" :disabled="loading">
          {{ loading ? '...' : 'Refresh' }}
        </button>
      </div>
    </nav>

    <div class="content">
      <!-- Simulation List -->
      <div class="sim-list">
        <div v-if="simulations.length === 0" class="empty-msg">
          {{ loading ? 'Loading...' : 'No simulations found' }}
        </div>
        <div
          v-for="sim in simulations"
          :key="sim.simulation_id"
          class="sim-card"
          :class="{ active: selectedSim?.simulation_id === sim.simulation_id }"
          @click="selectSimulation(sim)"
        >
          <div class="sim-card-header">
            <span class="sim-id">{{ sim.simulation_id }}</span>
            <span class="sim-status" :class="statusClass(sim.status)">{{ sim.status }}</span>
          </div>
          <div class="sim-card-meta">{{ sim.project_id }}</div>
        </div>
      </div>

      <!-- Detail Panel -->
      <div class="detail-panel" v-if="selectedSim">

        <!-- Run Status -->
        <div class="detail-section">
          <div class="section-header">
            <span class="section-title">Run Status</span>
            <button class="small-btn" @click="fetchRunStatus" :disabled="statusLoading">
              {{ statusLoading ? '...' : 'Refresh' }}
            </button>
          </div>

          <div v-if="runStatus" class="status-grid">
            <div class="stat-item">
              <span class="stat-label">Runner</span>
              <span class="stat-value" :class="runnerClass">{{ runStatus.runner_status }}</span>
            </div>
            <div class="stat-item">
              <span class="stat-label">Progress</span>
              <span class="stat-value">{{ (runStatus.progress_percent || 0).toFixed(1) }}%</span>
            </div>
            <div class="stat-item">
              <span class="stat-label">Round</span>
              <span class="stat-value">{{ runStatus.current_round || 0 }} / {{ runStatus.total_rounds || '?' }}</span>
            </div>
            <div class="stat-item">
              <span class="stat-label">Simulated</span>
              <span class="stat-value">{{ (runStatus.simulated_hours || 0).toFixed(1) }}h</span>
            </div>
            <div class="stat-item">
              <span class="stat-label">Twitter</span>
              <span class="stat-value">{{ runStatus.twitter_actions_count || 0 }} actions</span>
            </div>
            <div class="stat-item">
              <span class="stat-label">Reddit</span>
              <span class="stat-value">{{ runStatus.reddit_actions_count || 0 }} actions</span>
            </div>
            <div class="stat-item">
              <span class="stat-label">Total</span>
              <span class="stat-value highlight">{{ runStatus.total_actions_count || 0 }}</span>
            </div>
            <div class="stat-item">
              <span class="stat-label">Started</span>
              <span class="stat-value small">{{ formatTime(runStatus.started_at) }}</span>
            </div>
          </div>

          <div v-if="runStatus?.error" class="error-box">
            <div class="error-header">Error</div>
            <pre class="error-content">{{ runStatus.error }}</pre>
          </div>

          <div v-if="!runStatus && !statusLoading" class="no-data">
            No run status yet.
          </div>
        </div>

        <!-- Actions -->
        <div class="detail-section">
          <div class="section-header">
            <span class="section-title">Actions</span>
          </div>
          <div class="action-buttons">
            <button
              class="action-btn stop"
              @click="handleStop"
              :disabled="actionLoading"
              v-if="runStatus && ['running', 'starting'].includes(runStatus.runner_status)"
            >
              Stop
            </button>
            <button
              class="action-btn start"
              @click="handleForceStart"
              :disabled="actionLoading"
              v-if="!runStatus || ['failed', 'stopped', 'completed'].includes(runStatus.runner_status)"
            >
              Force Start
            </button>
            <button
              class="action-btn delete"
              @click="handleDelete"
              :disabled="actionLoading"
            >
              Delete
            </button>
          </div>
          <div v-if="actionMessage" class="action-message" :class="actionMessageClass">
            {{ actionMessage }}
          </div>
        </div>

        <!-- Log Viewer -->
        <div class="detail-section">
          <div class="section-header">
            <span class="section-title">Logs</span>
            <div class="log-controls">
              <label class="log-tail-label">
                Lines:
                <input v-model.number="logLines" type="number" min="50" max="2000" step="50" class="log-lines-input" />
              </label>
              <button class="small-btn" @click="fetchLogs" :disabled="logsLoading">
                {{ logsLoading ? '...' : 'Load' }}
              </button>
            </div>
          </div>
          <pre v-if="logContent" class="log-content" ref="logEl">{{ logContent }}</pre>
          <div v-else-if="!logsLoading" class="no-data">Click Load to fetch logs.</div>
        </div>

        <!-- Recent Actions -->
        <div class="detail-section" v-if="recentActions.length > 0">
          <div class="section-header">
            <span class="section-title">Recent Actions</span>
          </div>
          <div class="actions-list">
            <div v-for="(action, i) in recentActions" :key="i" class="action-item">
              <span class="action-platform" :class="action.platform">{{ action.platform }}</span>
              <span class="action-agent">{{ action.agent_name }}</span>
              <span class="action-type">{{ action.action_type }}</span>
              <span class="action-round">R{{ action.round }}</span>
            </div>
          </div>
        </div>

      </div>

      <div class="detail-panel empty" v-else>
        <div class="empty-msg">Select a simulation to view details</div>
      </div>
    </div>

    <!-- Delete Confirm Dialog -->
    <div v-if="showDeleteConfirm" class="dialog-overlay" @click.self="showDeleteConfirm = false">
      <div class="dialog">
        <div class="dialog-title">Delete Simulation?</div>
        <div class="dialog-body">
          <code>{{ selectedSim?.simulation_id }}</code><br><br>
          This will stop the process and permanently delete all files, logs, and simulation data.
        </div>
        <div class="dialog-actions">
          <button class="action-btn" @click="showDeleteConfirm = false">Cancel</button>
          <button class="action-btn delete" @click="confirmDelete" :disabled="actionLoading">
            {{ actionLoading ? '...' : 'Delete' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import {
  listSimulations,
  getRunStatus,
  getRunStatusDetail,
  stopSimulation,
  startSimulation,
  getSimulationLogs,
  deleteSimulation,
} from '../api/simulation'

const router = useRouter()

const simulations = ref([])
const selectedSim = ref(null)
const runStatus = ref(null)
const recentActions = ref([])
const logContent = ref('')
const logLines = ref(200)
const loading = ref(false)
const statusLoading = ref(false)
const logsLoading = ref(false)
const actionLoading = ref(false)
const actionMessage = ref('')
const actionMessageClass = ref('')
const showDeleteConfirm = ref(false)
const logEl = ref(null)

let pollTimer = null

const runnerClass = computed(() => {
  const s = runStatus.value?.runner_status
  if (!s) return ''
  const map = { running: 'status-running', completed: 'status-completed', failed: 'status-failed', starting: 'status-starting', stopped: 'status-stopped' }
  return map[s] || ''
})

function statusClass(status) {
  if (!status) return ''
  const s = status.toLowerCase()
  if (['running', 'simulating'].includes(s)) return 'status-running'
  if (s === 'completed') return 'status-completed'
  if (s === 'failed') return 'status-failed'
  if (s === 'ready') return 'status-ready'
  return ''
}

function formatTime(ts) {
  if (!ts) return '-'
  try { return new Date(ts).toLocaleString() } catch { return ts }
}

async function fetchSimulations() {
  loading.value = true
  try {
    const res = await listSimulations()
    simulations.value = res.data?.simulations || res.data || []
  } catch (e) {
    console.error('Failed to fetch simulations:', e)
  } finally {
    loading.value = false
  }
}

async function selectSimulation(sim) {
  selectedSim.value = sim
  runStatus.value = null
  recentActions.value = []
  logContent.value = ''
  actionMessage.value = ''
  await fetchRunStatus()
}

async function fetchRunStatus() {
  if (!selectedSim.value) return
  statusLoading.value = true
  try {
    const res = await getRunStatusDetail(selectedSim.value.simulation_id)
    runStatus.value = res.data
    recentActions.value = res.data?.recent_actions || []
  } catch {
    try {
      const res = await getRunStatus(selectedSim.value.simulation_id)
      runStatus.value = res.data
    } catch {
      runStatus.value = null
    }
  } finally {
    statusLoading.value = false
  }
}

async function fetchLogs() {
  if (!selectedSim.value) return
  logsLoading.value = true
  try {
    const res = await getSimulationLogs(selectedSim.value.simulation_id, logLines.value)
    logContent.value = res.data?.log || '(empty)'
    await nextTick()
    if (logEl.value) logEl.value.scrollTop = logEl.value.scrollHeight
  } catch (e) {
    logContent.value = 'Failed to load logs: ' + (e.message || e)
  } finally {
    logsLoading.value = false
  }
}

async function handleStop() {
  actionLoading.value = true
  actionMessage.value = ''
  try {
    await stopSimulation({ simulation_id: selectedSim.value.simulation_id })
    actionMessage.value = 'Stopped.'
    actionMessageClass.value = 'success'
    await fetchRunStatus()
  } catch (e) {
    actionMessage.value = e.response?.data?.error || e.message || 'Stop failed'
    actionMessageClass.value = 'error'
  } finally {
    actionLoading.value = false
  }
}

async function handleForceStart() {
  actionLoading.value = true
  actionMessage.value = ''
  try {
    await startSimulation({ simulation_id: selectedSim.value.simulation_id, force: true })
    actionMessage.value = 'Started (force).'
    actionMessageClass.value = 'success'
    await fetchRunStatus()
  } catch (e) {
    actionMessage.value = e.response?.data?.error || e.message || 'Start failed'
    actionMessageClass.value = 'error'
  } finally {
    actionLoading.value = false
  }
}

function handleDelete() {
  showDeleteConfirm.value = true
}

async function confirmDelete() {
  actionLoading.value = true
  actionMessage.value = ''
  try {
    await deleteSimulation({ simulation_id: selectedSim.value.simulation_id })
    showDeleteConfirm.value = false
    selectedSim.value = null
    runStatus.value = null
    logContent.value = ''
    await fetchSimulations()
  } catch (e) {
    actionMessage.value = e.response?.data?.error || e.message || 'Delete failed'
    actionMessageClass.value = 'error'
    showDeleteConfirm.value = false
  } finally {
    actionLoading.value = false
  }
}

function startPolling() {
  pollTimer = setInterval(() => {
    if (selectedSim.value && runStatus.value?.runner_status === 'running') {
      fetchRunStatus()
    }
  }, 5000)
}

onMounted(() => { fetchSimulations(); startPolling() })
onUnmounted(() => { if (pollTimer) clearInterval(pollTimer) })
</script>

<style scoped>
.monitor-container {
  --black: #000000;
  --white: #FFFFFF;
  --orange: #FF4500;
  --gray-light: #F5F5F5;
  --gray-text: #666666;
  --border: #E5E5E5;
  --font-mono: 'JetBrains Mono', monospace;
  --font-sans: 'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif;
  min-height: 100vh;
  background: var(--white);
  color: var(--black);
  font-family: var(--font-sans);
}

.navbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  height: 48px;
  background: var(--black);
  color: var(--white);
  font-family: var(--font-mono);
}
.nav-brand { font-weight: 700; font-size: 14px; letter-spacing: 2px; cursor: pointer; }
.nav-title { font-size: 13px; opacity: 0.7; }
.refresh-btn {
  background: transparent; border: 1px solid rgba(255,255,255,0.3); color: var(--white);
  padding: 4px 12px; font-size: 12px; font-family: var(--font-mono); cursor: pointer;
}
.refresh-btn:hover { border-color: var(--orange); }
.refresh-btn:disabled { opacity: 0.4; cursor: not-allowed; }

.content { display: flex; height: calc(100vh - 48px); }

/* List */
.sim-list {
  width: 280px; min-width: 280px;
  border-right: 1px solid var(--border);
  overflow-y: auto; padding: 12px;
}
.sim-card {
  padding: 10px 12px; border: 1px solid var(--border);
  margin-bottom: 8px; cursor: pointer; transition: border-color 0.15s;
}
.sim-card:hover { border-color: var(--black); }
.sim-card.active { border-color: var(--orange); border-width: 2px; }
.sim-card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 3px; }
.sim-id { font-family: var(--font-mono); font-size: 11px; font-weight: 600; word-break: break-all; }
.sim-status { font-family: var(--font-mono); font-size: 10px; padding: 2px 6px; border: 1px solid var(--border); white-space: nowrap; }
.sim-card-meta { font-size: 10px; color: var(--gray-text); font-family: var(--font-mono); word-break: break-all; }

/* Status colors */
.status-running { color: var(--orange); border-color: var(--orange) !important; }
.status-completed { color: #22c55e; border-color: #22c55e !important; }
.status-failed { color: #ef4444; border-color: #ef4444 !important; }
.status-ready { color: #3b82f6; border-color: #3b82f6 !important; }
.status-starting { color: #f59e0b; border-color: #f59e0b !important; }
.status-stopped { color: var(--gray-text); border-color: var(--gray-text) !important; }

/* Detail */
.detail-panel { flex: 1; overflow-y: auto; padding: 20px 24px; }
.detail-panel.empty { display: flex; align-items: center; justify-content: center; }
.empty-msg { color: var(--gray-text); font-family: var(--font-mono); font-size: 13px; }

.detail-section { margin-bottom: 24px; padding-bottom: 20px; border-bottom: 1px solid var(--border); }
.detail-section:last-child { border-bottom: none; }

.section-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; }
.section-title { font-weight: 700; font-size: 13px; text-transform: uppercase; letter-spacing: 1px; }

.small-btn {
  background: transparent; border: 1px solid var(--border);
  padding: 2px 10px; font-size: 11px; font-family: var(--font-mono); cursor: pointer;
}
.small-btn:hover { border-color: var(--black); }
.small-btn:disabled { opacity: 0.4; }

/* Stats */
.status-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 10px; }
.stat-item { display: flex; flex-direction: column; padding: 10px 12px; border: 1px solid var(--border); }
.stat-label { font-size: 10px; color: var(--gray-text); font-family: var(--font-mono); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; }
.stat-value { font-size: 15px; font-weight: 600; font-family: var(--font-mono); }
.stat-value.highlight { color: var(--orange); }
.stat-value.small { font-size: 11px; font-weight: 400; }

/* Error */
.error-box { margin-top: 14px; border: 1px solid #ef4444; }
.error-header { background: #ef4444; color: white; padding: 5px 12px; font-size: 11px; font-weight: 600; font-family: var(--font-mono); text-transform: uppercase; }
.error-content { padding: 12px; font-size: 11px; font-family: var(--font-mono); overflow-x: auto; max-height: 300px; overflow-y: auto; white-space: pre-wrap; word-break: break-word; background: #fef2f2; margin: 0; line-height: 1.5; }

.no-data { color: var(--gray-text); font-size: 12px; font-family: var(--font-mono); }

/* Action buttons */
.action-buttons { display: flex; gap: 8px; flex-wrap: wrap; }
.action-btn {
  padding: 7px 18px; font-size: 12px; font-family: var(--font-mono);
  font-weight: 600; border: none; cursor: pointer; transition: opacity 0.15s;
}
.action-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.action-btn.stop { background: #ef4444; color: white; }
.action-btn.stop:hover:not(:disabled) { opacity: 0.85; }
.action-btn.start { background: var(--black); color: white; }
.action-btn.start:hover:not(:disabled) { opacity: 0.8; }
.action-btn.delete { background: transparent; border: 1px solid #ef4444; color: #ef4444; }
.action-btn.delete:hover:not(:disabled) { background: #ef4444; color: white; }

.action-message { margin-top: 8px; font-size: 12px; font-family: var(--font-mono); }
.action-message.success { color: #22c55e; }
.action-message.error { color: #ef4444; }

/* Log viewer */
.log-controls { display: flex; align-items: center; gap: 8px; }
.log-tail-label { font-size: 11px; font-family: var(--font-mono); color: var(--gray-text); display: flex; align-items: center; gap: 4px; }
.log-lines-input {
  width: 60px; padding: 2px 6px; font-size: 11px; font-family: var(--font-mono);
  border: 1px solid var(--border); background: transparent; text-align: center;
}
.log-content {
  background: #0d1117; color: #e6edf3; padding: 14px; font-size: 11px;
  font-family: var(--font-mono); line-height: 1.5; max-height: 500px;
  overflow-y: auto; white-space: pre-wrap; word-break: break-word; margin: 0;
}

/* Recent actions */
.actions-list { max-height: 250px; overflow-y: auto; }
.action-item { display: flex; align-items: center; gap: 8px; padding: 5px 0; border-bottom: 1px solid var(--gray-light); font-family: var(--font-mono); font-size: 11px; }
.action-platform { padding: 1px 5px; font-size: 10px; text-transform: uppercase; border: 1px solid var(--border); }
.action-platform.twitter { color: #1d9bf0; border-color: #1d9bf0; }
.action-platform.reddit { color: var(--orange); border-color: var(--orange); }
.action-agent { font-weight: 600; min-width: 100px; }
.action-type { color: var(--gray-text); }
.action-round { margin-left: auto; color: var(--gray-text); }

/* Delete dialog */
.dialog-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,0.5);
  display: flex; align-items: center; justify-content: center; z-index: 100;
}
.dialog {
  background: var(--white); border: 1px solid var(--black);
  padding: 24px; max-width: 420px; width: 90%;
}
.dialog-title { font-weight: 700; font-size: 16px; margin-bottom: 12px; }
.dialog-body { font-size: 13px; color: var(--gray-text); line-height: 1.6; margin-bottom: 20px; }
.dialog-body code { font-family: var(--font-mono); color: var(--black); font-size: 12px; }
.dialog-actions { display: flex; gap: 8px; justify-content: flex-end; }
</style>
