<template>
  <div class="sports-page">
    <header class="sports-header">
      <RouterLink to="/" class="brand">MIROFISH</RouterLink>
      <nav><RouterLink to="/">Main workflow</RouterLink></nav>
    </header>

    <main class="sports-content">
      <div class="page-intro">
        <p class="eyebrow">SPORTS / HISTORICAL CHECK</p>
        <h1>Sports evaluation</h1>
        <p>Import completed league scores, then compare walk-forward probabilities. This check runs the statistical baselines without agents, Zep, or model tokens. Lower log loss and Brier scores are better.</p>
      </div>

      <div v-if="errorMessage" class="message error" role="alert">{{ errorMessage }}</div>
      <div v-if="successMessage" class="message success" role="status">{{ successMessage }}</div>

      <section class="card">
        <div class="section-heading">
          <div>
            <p class="eyebrow">01 / MIROFISH WORKFLOW</p>
            <h2>Start a match simulation</h2>
          </div>
        </div>
        <p class="note">Choose a provider fixture. MiroFish turns a pre-cutoff match dossier into its existing graph, synthetic agent simulation, and report workflow. Simulated perspectives are never real statements from coaches or players.</p>
        <form class="import-grid" @submit.prevent="createDossier">
          <label>
            League
            <select v-model="selectedLeague">
              <option value="EPL">Premier League (EPL)</option>
              <option value="NPFL">Nigeria Premier Football League (NPFL)</option>
              <option value="NBA">NBA basketball</option>
            </select>
          </label>
          <label>
            Provider match ID
            <input v-model.trim="fixtureId" type="text" inputmode="numeric" pattern="[0-9]+" placeholder="Numeric API-Sports fixture ID" required>
          </label>
          <label>
            Provider's listed kickoff, UTC
            <input v-model.trim="verifiedKickoffUtc" type="text" placeholder="2026-10-20T15:00:00Z" required>
          </label>
          <label>
            Cutoff hours before kickoff
            <input v-model.number="dossierCutoffHours" type="number" min="1" max="168" step="1" required>
          </label>
          <button class="primary" type="submit" :disabled="creatingDossier || !fixtureId || !verifiedKickoffUtc">
            {{ creatingDossier ? 'Creating dossier…' : 'Create match project' }}
          </button>
        </form>
        <p class="note">The provider match ID and UTC kickoff must agree with a source snapshot captured by the cutoff. A live sports API key and local model are needed to create the dossier; the later graph and simulation also require MiroFish's existing Zep memory setup.</p>
      </section>

      <section class="card">
        <div class="section-heading">
          <div>
            <p class="eyebrow">02 / SOURCE HISTORY</p>
            <h2>Imported final scores</h2>
          </div>
          <button class="secondary" type="button" :disabled="loadingLeagues" @click="loadLeagues">Refresh</button>
        </div>
        <p class="note">Historical scores are stored as immutable local snapshots. This import does not provide archived pre-match injuries, lineups, or odds.</p>
        <div class="league-grid">
          <button
            v-for="item in leagues"
            :key="item.league"
            type="button"
            class="league-card"
            :class="{ selected: selectedLeague === item.league }"
            @click="selectedLeague = item.league"
          >
            <strong>{{ item.league }}</strong>
            <span>{{ item.match_count }} final scores</span>
            <small>{{ item.seasons.length ? item.seasons.join(', ') : 'No history imported' }}</small>
          </button>
        </div>

        <form class="import-grid" @submit.prevent="importHistory">
          <label>
            Season
            <input v-model.trim="season" type="text" :placeholder="selectedLeague === 'NBA' ? '2026' : '2025-26'" required>
          </label>
          <label>
            Source URL for this file
            <input v-model.trim="sourceUrl" type="url" placeholder="https://..." required>
          </label>
          <label>
            Local {{ selectedLeague === 'NBA' ? 'hoopR schedule CSV' : 'OpenFootball text' }} file
            <input :key="selectedLeague" type="file" :accept="selectedLeague === 'NBA' ? '.csv,text/csv' : '.txt,text/plain'" required @change="chooseFile">
          </label>
          <button class="primary" type="submit" :disabled="importing || !selectedFile || !season || !sourceUrl">
            {{ importing ? 'Importing…' : 'Import final scores' }}
          </button>
        </form>
        <p class="note">Select a file you obtained from the source URL. The app records the import time and refuses conflicting final scores.</p>
      </section>

      <section class="card">
        <div class="section-heading">
          <div>
            <p class="eyebrow">03 / WALK-FORWARD</p>
            <h2>Evaluate {{ selectedLeague }}</h2>
          </div>
          <span class="count-tag">{{ selectedSummary?.match_count || 0 }} available matches</span>
        </div>
        <p class="note">For date-only sources, each match uses 00:00 UTC on its listed date minus the cutoff. Matches on the same date are forecast together. Results train the models only after their conservative availability time.</p>
        <form class="evaluation-controls" @submit.prevent="runEvaluation">
          <label>
            Hours before match date
            <input v-model.number="cutoffHours" type="number" min="1" max="168" step="1" required>
          </label>
          <label>
            Evaluate from (optional)
            <input v-model="evaluationStart" type="date">
          </label>
          <button class="primary" type="submit" :disabled="running || !selectedSummary?.match_count">
            {{ running ? 'Evaluating…' : 'Run historical check' }}
          </button>
        </form>
        <p class="note">An evaluation start date holds out later matches for scoring while earlier results warm the models as they become available.</p>
      </section>

      <section v-if="report" class="card" aria-live="polite">
        <p class="eyebrow">04 / RESULTS</p>
        <h2>{{ report.league }}: {{ report.match_count }} scored matches</h2>
        <p class="note">{{ report.history_match_count }} imported matches · {{ report.cutoff_hours }}-hour cutoff · {{ report.evaluation_start ? `from ${report.evaluation_start}` : 'full history' }}</p>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Probability source</th><th>Scored matches</th><th>Log loss ↓</th><th>Brier ↓</th><th>Status</th></tr></thead>
            <tbody>
              <tr v-for="[key, metric] in Object.entries(report.variants)" :key="key">
                <th scope="row">{{ variantLabel(key) }}</th>
                <td>{{ metric.count }}</td>
                <td>{{ formatMetric(metric.log_loss) }}</td>
                <td>{{ formatMetric(metric.brier_score) }}</td>
                <td>{{ metric.status === 'scored' ? 'Scored' : metric.reason }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="note">Elo and Dixon–Coles are separate football baselines; “Combined baseline” averages their probabilities. Agent and bookmaker rows remain unavailable until eligible pre-cutoff predictions or odds are supplied. No edge or profitability is implied.</p>

        <div v-for="[key, comparison] in Object.entries(report.paired_comparisons)" :key="key" class="comparison">
          <strong>{{ key === 'agents_vs_baseline' ? 'Agents against baseline' : 'Bookmaker against baseline' }}</strong>
          <span v-if="comparison.status === 'scored'">
            {{ comparison.count }} matched matches · log-loss difference {{ formatSigned(comparison.delta_log_loss) }} · Brier difference {{ formatSigned(comparison.delta_brier_score) }}
          </span>
          <span v-else>Unavailable: {{ comparison.reason }}</span>
        </div>

        <details class="calibration-details">
          <summary>Show calibration by outcome</summary>
          <div v-for="[variant, metric] in Object.entries(report.variants)" :key="variant">
            <template v-if="metric.status === 'scored'">
              <h3>{{ variantLabel(variant) }}</h3>
              <div v-for="[outcome, bins] in Object.entries(metric.calibration)" :key="outcome" class="calibration-group">
                <h4>{{ outcome }}</h4>
                <div class="table-wrap">
                  <table>
                    <thead><tr><th>Probability band</th><th>Matches</th><th>Mean forecast</th><th>Observed frequency</th></tr></thead>
                    <tbody>
                      <tr v-for="bin in bins" :key="bin.lower">
                        <td>{{ Math.round(bin.lower * 100) }}–{{ Math.round(bin.upper * 100) }}%</td>
                        <td>{{ bin.count }}</td>
                        <td>{{ formatPercent(bin.mean_predicted) }}</td>
                        <td>{{ formatPercent(bin.observed_frequency) }}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>
            </template>
          </div>
        </details>
        <p class="note">{{ report.result_availability_assumption }}</p>
      </section>
    </main>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { createSportsDossier, importSportsHistory, listSportsHistory, runSportsEvaluation } from '../api/sportsEvaluation'

const router = useRouter()
const leagues = ref([])
const selectedLeague = ref('EPL')
const season = ref('2025-26')
const sourceUrl = ref('')
const selectedFile = ref(null)
const cutoffHours = ref(24)
const evaluationStart = ref('')
const report = ref(null)
const fixtureId = ref('')
const verifiedKickoffUtc = ref('')
const dossierCutoffHours = ref(24)
const errorMessage = ref('')
const successMessage = ref('')
const loadingLeagues = ref(false)
const importing = ref(false)
const running = ref(false)
const creatingDossier = ref(false)

const selectedSummary = computed(() => leagues.value.find(item => item.league === selectedLeague.value))
const sport = computed(() => selectedLeague.value === 'NBA' ? 'basketball' : 'football')

function suggestedSource() {
  if (selectedLeague.value === 'EPL') return `https://raw.githubusercontent.com/openfootball/england/master/${season.value}/1-premierleague.txt`
  if (selectedLeague.value === 'NPFL') return `https://raw.githubusercontent.com/openfootball/world/master/africa/nigeria/${season.value}_ng1.txt`
  return `https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_nba_schedules/nba_schedule_${season.value}.csv`
}

watch(selectedLeague, league => {
  season.value = league === 'NBA' ? '2026' : '2025-26'
  selectedFile.value = null
  report.value = null
  errorMessage.value = ''
})
watch([selectedLeague, season], () => { sourceUrl.value = suggestedSource() }, { immediate: true })

function chooseFile(event) {
  selectedFile.value = event.target.files?.[0] || null
}

async function createDossier() {
  creatingDossier.value = true
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const response = await createSportsDossier({
      sport: sport.value,
      league: sport.value === 'football' ? selectedLeague.value.toLowerCase() : null,
      match_id: fixtureId.value,
      kickoff_at: verifiedKickoffUtc.value,
      cutoff_hours: Number(dossierCutoffHours.value)
    })
    await router.push({ name: 'Process', params: { projectId: response.data.project_id } })
  } catch (error) {
    errorMessage.value = error.message || 'Could not create a match project.'
  } finally {
    creatingDossier.value = false
  }
}

async function loadLeagues() {
  loadingLeagues.value = true
  errorMessage.value = ''
  try {
    const response = await listSportsHistory()
    leagues.value = response.data.leagues
  } catch (error) {
    errorMessage.value = error.message || 'Could not load imported history.'
  } finally {
    loadingLeagues.value = false
  }
}

async function importHistory() {
  if (!selectedFile.value) return
  importing.value = true
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const data = new FormData()
    data.append('sport', sport.value)
    data.append('league', selectedLeague.value)
    data.append('season', season.value)
    data.append('source', sourceUrl.value)
    data.append('file', selectedFile.value)
    const response = await importSportsHistory(data)
    successMessage.value = `Imported ${response.data.imported_match_count} ${selectedLeague.value} finals. Snapshot ${response.data.snapshot_id}.`
    report.value = null
    await loadLeagues()
  } catch (error) {
    errorMessage.value = error.message || 'Could not import this source file.'
  } finally {
    importing.value = false
  }
}

async function runEvaluation() {
  running.value = true
  errorMessage.value = ''
  successMessage.value = ''
  report.value = null
  try {
    const response = await runSportsEvaluation({
      sport: sport.value,
      league: selectedLeague.value,
      cutoff_hours: Number(cutoffHours.value),
      evaluation_start: evaluationStart.value || null
    })
    report.value = response.data
  } catch (error) {
    errorMessage.value = error.message || 'Could not evaluate the imported history.'
  } finally {
    running.value = false
  }
}

function variantLabel(variant) {
  return {
    naive: 'Naive base rate', elo: 'Elo', dixon_coles: 'Dixon–Coles',
    combined: 'Combined baseline', baseline_plus_agents: 'Baseline + agents',
    bookmaker: 'Bookmaker (de-vigged)'
  }[variant] || variant
}

function formatMetric(value) { return typeof value === 'number' ? value.toFixed(4) : '—' }
function formatSigned(value) { return typeof value === 'number' ? `${value >= 0 ? '+' : ''}${value.toFixed(4)}` : '—' }
function formatPercent(value) { return typeof value === 'number' ? `${(value * 100).toFixed(1)}%` : '—' }

onMounted(loadLeagues)
</script>

<style scoped>
.sports-page { min-height: 100vh; background: #f6f6f3; color: #181818; }
.sports-header { background: #101010; color: white; min-height: 60px; padding: 0 clamp(20px, 4vw, 48px); display: flex; align-items: center; justify-content: space-between; }
.sports-header a { color: white; text-decoration: none; }
.sports-header .brand { font-weight: 800; letter-spacing: 1px; font-size: 1.2rem; }
.sports-header nav a { font-size: .9rem; }
.sports-content { max-width: 1100px; margin: 0 auto; padding: 44px 24px 80px; }
.page-intro { max-width: 790px; margin-bottom: 32px; }
.eyebrow { font-size: .73rem; letter-spacing: .1em; color: #af4c1c; font-weight: 800; margin-bottom: 10px; }
h1 { font-size: clamp(2rem, 5vw, 3.2rem); line-height: 1.1; margin-bottom: 16px; }
h2 { font-size: 1.4rem; margin-bottom: 9px; }
h3 { margin: 24px 0 12px; }
h4 { text-transform: capitalize; margin: 18px 0 8px; }
.page-intro p:last-child, .note { color: #555; line-height: 1.55; font-size: .93rem; }
.card { background: white; border: 1px solid #d8d8d5; padding: 28px; margin: 20px 0; }
.section-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; }
.league-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin: 22px 0; }
.league-card { border: 1px solid #c9c9c6; background: #fafaf8; padding: 16px; text-align: left; display: flex; flex-direction: column; gap: 6px; cursor: pointer; color: #181818; }
.league-card.selected { border-color: #c85a22; box-shadow: inset 0 0 0 1px #c85a22; background: #fff8f4; }
.league-card strong { font-size: 1.1rem; }
.league-card span, .league-card small { color: #555; overflow-wrap: anywhere; }
.import-grid, .evaluation-controls { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; align-items: end; margin: 22px 0 12px; }
label { display: flex; flex-direction: column; gap: 8px; font-size: .84rem; font-weight: 700; }
input, select { width: 100%; min-height: 42px; border: 1px solid #bbb; border-radius: 0; padding: 10px; font: inherit; font-size: .9rem; background: white; color: #181818; }
input[type='file'] { padding: 8px; }
button { font: inherit; }
.primary, .secondary { min-height: 43px; border: 1px solid #141414; padding: 10px 18px; cursor: pointer; }
.primary { background: #141414; color: white; font-weight: 700; }
.secondary { background: white; color: #141414; }
button:disabled { opacity: .5; cursor: not-allowed; }
.count-tag { border: 1px solid #d5d5d0; padding: 8px 12px; white-space: nowrap; font-size: .8rem; }
.message { padding: 12px 16px; margin-bottom: 16px; line-height: 1.4; }
.message.error { border: 1px solid #b6342e; background: #fff2f0; color: #8c221d; }
.message.success { border: 1px solid #348c54; background: #effaf1; color: #196435; overflow-wrap: anywhere; }
.table-wrap { width: 100%; overflow-x: auto; margin: 18px 0; }
table { width: 100%; border-collapse: collapse; text-align: left; font-size: .87rem; }
th, td { border-bottom: 1px solid #dededb; padding: 12px 10px; vertical-align: top; }
thead th { background: #f4f4f1; white-space: nowrap; }
tbody th { font-weight: 700; }
.comparison { border-top: 1px solid #dededb; padding: 14px 0; display: flex; flex-direction: column; gap: 6px; font-size: .9rem; }
.calibration-details { margin: 20px 0; border-top: 1px solid #dededb; padding-top: 14px; }
summary { cursor: pointer; font-weight: 700; }
@media (max-width: 700px) {
  .sports-content { padding: 28px 16px 60px; }
  .card { padding: 20px; }
  .league-grid, .import-grid, .evaluation-controls { grid-template-columns: 1fr; }
  .section-heading { flex-direction: column; }
}
</style>
