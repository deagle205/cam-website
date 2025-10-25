import "./CompanyLogin.css"
import { useState } from "react";
import { Link } from 'react-router-dom';


const CompanyLogin = () => {
    const [password, setPassword] = useState("");
    const [username, setUsername] = useState("");
  const companyusernameInputProps = {
        className: "company-username-input",
    value: username,
    onChange: (e: React.ChangeEvent<HTMLInputElement>) =>
      setUsername(e.target.value),
    placeholder: "Company Username",
  };
  const companypasswordInputProps = {
      className: "company-password-input",
    type: "password",
    value: password,
    onChange: (e: React.ChangeEvent<HTMLInputElement>) =>
      setPassword(e.target.value),
    placeholder: "Password",
  };

  return (
    <>
      <form className="company-login-form" onSubmit={() => {}}>
        <input {...companyusernameInputProps} />
        <input {...companypasswordInputProps} />
        <button type="submit" className="sign-in-button">
          Sign In
        </button>
      </form>

      <Link to="/" className="button-style-class">
      Log in as a Student!
    </Link>
    </>

  );
};

export default CompanyLogin;