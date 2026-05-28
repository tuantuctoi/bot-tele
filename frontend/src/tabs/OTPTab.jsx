import { useState, useEffect, useRef } from 'react'
import { getCode } from '../api'

export default function OTPTab({ pendingRequestId }) {
  const [requestId, setRequestId] = useState(pendingRequestId || '')
  const [status, setStatus] = useState('idle') // idle | polling | done | expired | error
  const [result, setResult] = useState(null)
  const [attempt, setAttempt] = useState(0)
  const [error, setError] = useState('')
  const timerRef = useRef(null)

  const MAX_ATTEMPTS = 60
  const INTERVAL_MS = 5000

  const stopPolling = () => {
    if (timerRef.current) clearInterval(timerRef.current)
    timerRef.current = null
  }

  const checkOnce = async (id, att) => {
    try {
      const data = await getCode(id)
      if (!data.success) {
        stopPolling()
        setError(data.message || 'Lỗi khi lấy OTP')
        setStatus('error')
        return
      }
      const d = data.data
      const s = d.Status
      if (s === 1) {
        stopPolling()
        setResult(d)
        setStatus('done')
      } else if (s === 2) {
        stopPolling()
        setStatus('expired')
      } else {
        setAttempt(att)
        if (att >= MAX_ATTEMPTS) {
          stopPolling()
          setStatus('error')
          setError('Hết thời gian chờ (5 phút)')
        }
      }
    } catch {
      stopPolling()
      setError('Lỗi kết nối')
      setStatus('error')
    }
  }

  const startPolling = (id) => {
    if (!id) return
    stopPolling()
    setResult(null)
    setError('')
    setAttempt(0)
    setStatus('polling')
    let att = 0
    checkOnce(id, att)
    timerRef.current = setInterval(() => {
      att++
      checkOnce(id, att)
    }, INTERVAL_MS)
  }

  useEffect(() => {
    if (pendingRequestId) {
      setRequestId(pendingRequestId)
      startPolling(pendingRequestId)
    }
    return () => stopPolling()
  }, [pendingRequestId])

  const copyText = (text) => navigator.clipboard?.writeText(text)

  return (
    <div>
      <div className="section-title">Lấy OTP</div>
      <div className="form-group">
        <label className="form-label">Request ID</label>
        <input
          className="form-input"
          placeholder="Nhập Request ID..."
          value={requestId}
          onChange={e => setRequestId(e.target.value)}
          disabled={status === 'polling'}
        />
      </div>

      {status === 'idle' || status === 'error' || status === 'expired' || status === 'done' ? (
        <button
          className="btn"
          onClick={() => startPolling(requestId)}
          disabled={!requestId || status === 'polling'}
        >
          Bắt đầu chờ OTP
        </button>
      ) : null}

      {status === 'polling' && (
        <>
          <div className="card" style={{ textAlign: 'center' }}>
            <span className="spinner" />
            Đang chờ OTP... (lần {attempt}/{MAX_ATTEMPTS})
          </div>
          <button className="btn danger" onClick={() => { stopPolling(); setStatus('idle') }}>
            Dừng
          </button>
        </>
      )}

      {error && <div className="error-msg">{error}</div>}

      {status === 'expired' && (
        <div className="card" style={{ borderLeft: '3px solid #ff453a' }}>
          <span className="badge badge-expired">Hết hạn</span>
          <div style={{ marginTop: 8, color: 'var(--tg-hint)' }}>Phiên thuê số đã hết hạn.</div>
        </div>
      )}

      {status === 'done' && result && (
        <div className="card" style={{ borderLeft: '3px solid #30d158' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <span className="badge badge-success">✅ Nhận được OTP</span>
          </div>
          <div className="otp-code">{result.Code}</div>
          <button className="btn secondary" onClick={() => copyText(result.Code)}>
            📋 Copy mã OTP
          </button>
          <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 6, fontSize: 14 }}>
            <div><strong>SĐT:</strong> {result.PhoneOriginal || ('0' + result.Phone)}</div>
            <div><strong>Dịch vụ:</strong> {result.ServiceName}</div>
            {result.IsSound === true || result.IsSound === 'true' ? (
              <div>
                <div className="form-label" style={{ marginTop: 8 }}>Nội dung (audio)</div>
                <audio controls src={result.SmsContent} />
              </div>
            ) : (
              <div>
                <div className="form-label" style={{ marginTop: 8 }}>Nội dung SMS</div>
                <div style={{ color: 'var(--tg-hint)', fontSize: 13, lineHeight: 1.5 }}>{result.SmsContent}</div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
