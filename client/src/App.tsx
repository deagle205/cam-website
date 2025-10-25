import Login from './pages/Login/Login.tsx'
import './App.css'
import CompanyLogin from './pages/CompanyLogin/CompanyLogin.tsx'
import { BrowserRouter, Routes, Route } from 'react-router-dom'; 

function App() {

  return (
    <div>
      <BrowserRouter>
        <Routes>
          
          <Route index element={<Login />} /> 
          
          
          <Route path='/CompanyLogin' element={<CompanyLogin />} />
        </Routes>
      </BrowserRouter>
    </div>
  );
}

export default App;