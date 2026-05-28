import { useState, useEffect } from 'react'
import { getNetworks, getServices, buyNumber } from '../api'

export default function Services({ onBought }) {
  const [country, setCountry] = useState('vn')
  const [networks, setNetworks] = useState([])
  const [services, setServices] = useState([])
  const [network, setNetwork] = useState('')
  const [prefix, setPrefix] = useState('')
  const [loading, setLoading] = useState(false)
  const [buying, setBuying] = useState(null)
  const [error, setError] = useState('')
  const [result, setResult] = useState(null)

  useEffect(() => {
    getNetworks().then(d => {
      if (d.success) {
        const vnNets = ['MOBIFONE', 'VINAPHONE', 'VIETTEL', 'VIETNAMOBILE', 'ITELECOM', 'WINTEL']
        const laNets = ['UNITEL', 'ETL', 'BEELINE', 'LAOTEL']
        setNetworks({ vn: vnNets, la: laNets })
      }
    })
  }, [])

  const loadServices = async () => {
    setLoading(true)
    setError('')
    setResult(null)
    try {
      const data = await getServices(country)
      if (data.success) setServices(data.data || [])
      else setError(data.message)
    } catch { setError('Lỗi kết nối') }
    setLoading(false)
  }

  useEffect(() => { loadServices() }, [country])

  const handleBuy = async (svc) => {
    setBuying(svc.id)
    setError('')
    setResult(null)
    try {
      const params = { serviceId: svc.id, country }
      if (network) params.network = network
      if (prefix) params.prefix = prefix
      const data = await buyNumber(params)
      if (data.success) {
        setResult(data.data)
        onBought && onBought(data.data.request_id)
      } else {
        setError(data.message || 'Thuê số thất bại')
      }
    } catch { setError('Lỗi kết nối') }
    setBuying(null)
  }

  const copyText = (text) => navigator.clipboard?.writeText(text)
  const nets = networks[country] || []

  return (
    <div>
      <div className="section-title">Thuê số</div>

      {/* Country */}
      <div className="form-group">
        <label className="form-label">Quốc gia</label>
        <select className="form-select" value={country} onChange={e => setCountry(e.target.value)}>
          <option value="vn">🇻🇳 Việt Nam</option>
          <option value="la">🇱🇦 Lào</option>
        </select>
      </div>

      {/* Network filter */}
      <div className="form-group">
        <label className="form-label">Nhà mạng (tuỳ chọn)</label>
        <select className="form-select" value={network} onChange={e => setNetwork(e.target.value)}>
          <option value="">Tất cả nhà mạng</option>
          {nets.map(n => <option key={n} value={n}>{n}</option>)}
        </select>
      </div>

      {/* Prefix filter */}
      <div className="form-group">
        <label className="form-label">Đầu số (tuỳ chọn, vd: 90|91)</label>
        <input className="form-input" placeholder="Bỏ trống = lấy tất cả" value={prefix}
          onChange={e => setPrefix(e.target.value)} />
      </div>

      {error && <div className="error-msg">{error}</div>}

      {/* Buy result */}
      {result && (
        <div className="card" style={{ marginBottom: 16, borderLeft: '3px solid #30d158' }}>
          <div className="card-title">✅ Thuê số thành công</div>
          <div style={{ marginTop: 6 }}>
            <strong>SĐT:</strong> 0{result.phone_number}
            <button className="copy-btn" onClick={() => copyText('0' + result.phone_number)}>Copy</button>
          </div>
          <div style={{ marginTop: 4 }}>
            <strong>Request ID:</strong> {result.request_id}
            <button className="copy-btn" onClick={() => copyText(result.request_id)}>Copy</button>
          </div>
          <div style={{ marginTop: 4, fontSize: 13, color: 'var(--tg-hint)' }}>
            Số dư còn: {Number(result.balance).toLocaleString('vi-VN')}đ
          </div>
        </div>
      )}

      {/* Service list */}
      <div className="section-title">Danh sách dịch vụ {loading && <span className="spinner" />}</div>
      {services.length === 0 && !loading && <div className="empty-state">Không có dịch vụ</div>}
      {services.map(s => (
        <div key={s.id} className="list-item">
          <div className="list-item-main">
            <div className="list-item-name">{s.name}</div>
            <div className="list-item-sub">ID: {s.id}</div>
          </div>
          <div className="list-item-price">{Number(s.price).toLocaleString('vi-VN')}đ</div>
          <button
            className="btn"
            style={{ width: 'auto', marginTop: 0, marginLeft: 12, padding: '8px 14px', fontSize: 13 }}
            onClick={() => handleBuy(s)}
            disabled={buying === s.id}
          >
            {buying === s.id ? '...' : 'Mua'}
          </button>
        </div>
      ))}
    </div>
  )
}
