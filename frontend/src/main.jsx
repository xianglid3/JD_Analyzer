import './index.css'

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import ProtectedRoute from './components/ProtectedRoute'

import LoginPage from './pages/LoginPage'
import DashboardPage from './pages/DashboardPage'
import SignupPage from './pages/SignupPage'
import ResumePage from './pages/ResumePage'
import JobDetailPage from './pages/JobDetailPage'

const queryClient = new QueryClient()

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <QueryClientProvider client = {queryClient}>
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

          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>
)
