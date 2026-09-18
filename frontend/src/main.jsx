import './index.css'

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import ProtectedRoute from './components/ProtectedRoute'
import ToastHost from './components/Toast'

import LoginPage from './pages/LoginPage'
import DashboardPage from './pages/DashboardPage'
import SignupPage from './pages/SignupPage'
import ResumePage from './pages/ResumePage'
import JobDetailPage from './pages/JobDetailPage'
import JobReviewPage from './pages/JobReviewPage'
import TailoringRunPage from './pages/TailoringRunPage'

const queryClient = new QueryClient()

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <QueryClientProvider client = {queryClient}>
      <ToastHost />
      <BrowserRouter>
        <Routes>
          
          <Route path="/login" element={<LoginPage />} />
          <Route path="/signup" element={<SignupPage />} />
          
          {/* need to protect /dashboard */}
          <Route path="/dashboard" element={ 
            <ProtectedRoute> <DashboardPage /> </ProtectedRoute>} /> 
          
          <Route path='/resume' element = {
            <ProtectedRoute> <ResumePage/> </ProtectedRoute>} />

          <Route path="/jobs/:id" element={
            <ProtectedRoute> <JobDetailPage /> </ProtectedRoute>} />

          <Route path="/jobs/review/:id" element={
            <ProtectedRoute> <JobReviewPage /> </ProtectedRoute>} />

          <Route path="/tailoring/:id" element={
            <ProtectedRoute> <TailoringRunPage /> </ProtectedRoute>} />

          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>
)
