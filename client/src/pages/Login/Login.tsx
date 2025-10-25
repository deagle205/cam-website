import "./Login.css";
import { useState } from "react";

const Login = () => {
    const [password, setPassword] = useState("");
    const [username, setUsername] = useState("");
  const usernameInputProps = {
        className: "username-input",
    value: username,
    onChange: (e: React.ChangeEvent<HTMLInputElement>) =>
      setUsername(e.target.value),
    placeholder: "Username",
  };
  const passwordInputProps = {
      className: "password-input",
    type: "password",
    value: password,
    onChange: (e: React.ChangeEvent<HTMLInputElement>) =>
      setPassword(e.target.value),
    placeholder: "Password",
  };

  return (
    <>
      <form className="login-form" onSubmit={() => {}}>
        <input {...usernameInputProps} />
        <input {...passwordInputProps} />
        <button type="submit" className="sign-in-button">
          Sign In
        </button>
      </form>
    </>
  );
};

export default Login;
