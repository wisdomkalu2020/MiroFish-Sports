import service from './index'

export function listSportsHistory() {
  return service({ url: '/api/sports/evaluation/leagues', method: 'get' })
}

export function importSportsHistory(formData) {
  return service({
    url: '/api/sports/evaluation/import',
    method: 'post',
    data: formData,
    headers: { 'Content-Type': 'multipart/form-data' }
  })
}

export function runSportsEvaluation(data) {
  return service({ url: '/api/sports/evaluation/run', method: 'post', data })
}

export function createSportsDossier(data) {
  return service({ url: '/api/sports/dossiers', method: 'post', data })
}
