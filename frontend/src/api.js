import WebApp from '@twa-dev/sdk'

const BASE = ''  // same origin, Flask serves both API and frontend

function getAuth() {
  return WebApp.initData || ''
}

async function apiFetch(path) {
  const res = await fetch(BASE + path, {
    headers: { Authorization: getAuth() },
  })
  return res.json()
}

export const getBalance = () => apiFetch('/api/balance')
export const getNetworks = () => apiFetch('/api/networks')
export const getServices = (country = 'vn') => apiFetch(`/api/services?country=${country}`)
export const buyNumber = (params) => {
  const qs = new URLSearchParams(Object.fromEntries(
    Object.entries(params).filter(([, v]) => v)
  )).toString()
  return apiFetch(`/api/buy?${qs}`)
}
export const getCode = (requestId) => apiFetch(`/api/code/${requestId}`)
export const getHistory = (params = {}) => {
  const qs = new URLSearchParams(Object.fromEntries(
    Object.entries(params).filter(([, v]) => v)
  )).toString()
  return apiFetch(`/api/history?${qs}`)
}
