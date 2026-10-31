<template>
  <section class="paper-panel">
    <h2>Odds and paper ledger</h2>
    <p>Hypothetical NGN 100/200 records only. No money is collected and no bets are placed. Quotes and predictions are timestamped now and cannot be edited.</p>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <p v-if="notice" role="status">{{ notice }}</p>
    <details>
      <summary>Capture {{ league }} odds</summary>
      <form @submit.prevent="captureManual" class="fields">
        <label>Match ID<input v-model.trim="quote.match_id" required></label>
        <label>Home team<input v-model.trim="quote.home_team" required></label>
        <label>Away team<input v-model.trim="quote.away_team" required></label>
        <label>Kickoff (ISO UTC)<input v-model.trim="quote.kickoff_at" placeholder="2026-11-15T15:00:00Z" required></label>
        <label>Bookmaker<input v-model.trim="quote.bookmaker" required></label>
        <label>Source URL or reference<input v-model.trim="quote.source" required></label>
        <label>Quote label<select v-model="quote.label"><option>current</option><option>opening</option><option>closing</option></select></label>
        <label v-for="outcome in outcomes" :key="outcome">{{ outcome }} decimal odds<input v-model.number="prices[outcome]" type="number" min="1.001" step="any" required></label>
        <button :disabled="busy">Save manual quote</button>
      </form>
      <button type="button" :disabled="busy || !quote.match_id || !quote.home_team || !quote.away_team || league === 'NPFL'" @click="captureProvider">Fetch current prices from The Odds API</button>
      <p>The provider option uses the server's configured key and exact team names. Leave the bookmaker field empty to request available bookmakers. A closing label records your observation; it does not independently verify the market close.</p>
    </details>
    <details>
      <summary>Record a saved forecast as a paper pick</summary>
      <form @submit.prevent="recordPick" class="fields">
        <label>Captured quote<select v-model="pick.quote_id" required><option value="">Choose a quote</option><option v-for="item in leagueQuotes" :key="item.quote_id" :value="item.quote_id">{{ item.home_team }} vs {{ item.away_team }} · {{ item.bookmaker }} · {{ item.recorded_at }}</option></select></label>
        <label>MiroFish project ID<input v-model.trim="pick.project_id" placeholder="proj_..." required></label>
        <label>Simulation ID (optional; blank uses baseline)<input v-model.trim="pick.simulation_id" placeholder="sim_..."></label>
        <label>Hypothetical outcome<select v-model="pick.pick"><option v-for="outcome in outcomes" :key="outcome">{{ outcome }}</option></select></label>
        <label>Hypothetical NGN amount<select v-model.number="pick.stake_ngn"><option :value="100">100</option><option :value="200">200</option></select></label>
        <button :disabled="busy || !pick.quote_id">Record paper prediction</button>
      </form>
      <p>Probabilities come from the saved MiroFish forecast; they cannot be entered or backdated here. The quote must match the project's teams, league and match ID.</p>
      <p v-if="selectedQuote">De-vigged market: {{ formatProbabilities(selectedQuote.market_implied_probabilities) }}</p>
    </details>
    <details>
      <summary>Correct kickoff or settle from imported scores</summary>
      <form @submit.prevent="correctKickoff" class="fields">
        <label>Ledger match ID<input v-model.trim="correction.match_id" required></label>
        <label>Corrected kickoff (ISO UTC)<input v-model.trim="correction.kickoff_at" required></label>
        <label>Correction source<input v-model.trim="correction.source" required></label>
        <button :disabled="busy">Append kickoff correction</button>
      </form>
      <form @submit.prevent="settle" class="fields">
        <label>Ledger match ID for settlement<input v-model.trim="settlement.match_id" required></label>
        <label>Imported final-score match ID<input v-model.trim="settlement.history_match_id" required></label>
        <button :disabled="busy">Settle from imported final</button>
      </form>
      <p>Settlement uses the selected {{ league }} history. Teams and dates must match. Corrections append new events; a prediction made after a corrected kickoff is void.</p>
    </details>
    <button type="button" :disabled="busy" @click="refresh">Refresh paper records</button>
    <p v-if="!records.length">No paper predictions recorded.</p>
    <div class="table-scroll" v-else>
      <table><thead><tr><th>Match / recorded</th><th>Pick / NGN</th><th>Model probability</th><th>Market probability</th><th>Status</th><th>Hypothetical net NGN</th><th>Closing line value</th></tr></thead>
        <tbody><tr v-for="item in records" :key="item.prediction_id"><td>{{ item.home_team }} vs {{ item.away_team }} ({{ item.league }})<br>{{ item.recorded_at }}</td><td>{{ item.pick }} / {{ item.stake_ngn }}</td><td>{{ percent(item.probabilities[item.pick]) }}</td><td>{{ percent(item.market_implied_probabilities[item.pick]) }}</td><td>{{ item.status }}</td><td>{{ item.net_ngn ?? '—' }}</td><td>{{ percent(item.closing_line_value_fraction) }}</td></tr></tbody>
      </table>
    </div>
  </section>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import service from '../api/index'
const props = defineProps({ league: { type: String, required: true } })
const sport = computed(() => props.league === 'NBA' ? 'basketball' : 'football')
const outcomes = computed(() => sport.value === 'football' ? ['home', 'draw', 'away'] : ['home', 'away'])
const quote = reactive({ match_id:'', home_team:'', away_team:'', bookmaker:'', kickoff_at:'', source:'', label:'current' })
const prices = reactive({ home:2, draw:3, away:3 })
const pick = reactive({ quote_id:'', project_id:'', simulation_id:'', pick:'home', stake_ngn:100 })
const correction = reactive({ match_id:'', kickoff_at:'', source:'' })
const settlement = reactive({ match_id:'', history_match_id:'' })
const quotes = ref([]), records = ref([]), busy = ref(false), error = ref(''), notice = ref('')
const leagueQuotes = computed(() => quotes.value.filter(item => item.league === props.league))
const selectedQuote = computed(() => leagueQuotes.value.find(item => item.quote_id === pick.quote_id))
const percent = value => value == null ? '—' : `${(value * 100).toFixed(2)}%`
const formatProbabilities = values => Object.entries(values).map(([key,value]) => `${key} ${percent(value)}`).join(' · ')
async function refresh() {
  error.value = ''
  try { const response = await service.get('/api/sports/paper'); quotes.value = response.data.quotes; records.value = response.data.predictions }
  catch (e) { error.value = e.message }
}
async function submit(path, data, message) {
  busy.value = true; error.value = ''; notice.value = ''
  try { const response = await service.post(`/api/sports/paper/${path}`, data); notice.value = message; await refresh(); return response.data }
  catch (e) { error.value = e.message }
  finally { busy.value = false }
}
async function captureManual() {
  const result = await submit('quotes', { ...quote, sport:sport.value, league:props.league, decimal_odds:Object.fromEntries(outcomes.value.map(key => [key,prices[key]])) }, 'Quote captured with the current server timestamp.')
  if (result) pick.quote_id = result.quote_id
}
async function captureProvider() {
  const result = await submit('provider-quotes', {match_id:quote.match_id,home_team:quote.home_team,away_team:quote.away_team,bookmaker:quote.bookmaker || null,label:quote.label,sport:sport.value,league:props.league}, 'Provider quotes captured.')
  if (result?.quotes.length) pick.quote_id = result.quotes[0].quote_id
}
const recordPick = () => submit('predictions', {...pick}, 'Hypothetical prediction recorded permanently.')
const correctKickoff = () => submit('kickoff', {...correction}, 'Kickoff correction appended.')
const settle = () => submit('settle', {...settlement,sport:sport.value,league:props.league}, 'Imported final recorded; paper settlement recalculated.')
watch(() => props.league, () => {pick.quote_id=''; pick.pick='home'; error.value=''; notice.value=''})
onMounted(refresh)
</script>

<style scoped>
.paper-panel { background:white; border:1px solid #ddd; padding:28px; margin:24px 0; }
p { line-height:1.6; color:#555; } details { border-top:1px solid #ddd; padding:18px 0; } summary { cursor:pointer; font-weight:700; }
.fields { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; margin:18px 0; } label { display:flex; flex-direction:column; gap:8px; font-size:.85rem; }
input,select,button { min-height:42px; padding:10px; border:1px solid #bbb; font:inherit; background:white; color:#181818; max-width:100%; } button { cursor:pointer; font-weight:700; } button:disabled { opacity:.55; cursor:default; }
.table-scroll { overflow:auto; } table { border-collapse:collapse; width:100%; margin-top:18px; } td,th { border-bottom:1px solid #ddd; text-align:left; padding:10px; font-size:.8rem; } .error { color:#a32222; }
@media(max-width:650px) { .fields { grid-template-columns:1fr; } .paper-panel {padding:18px;} }
</style>
