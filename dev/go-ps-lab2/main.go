package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os/exec"
)

// Request is what we send to PowerShell as JSON
type Request struct {
	Name   string `json:"name"`
	Number int    `json:"number"`
}

// Response is what we expect back from PowerShell as JSON
type Response struct {
	Message string `json:"message"`
	Name    string `json:"name"`
	Number  int    `json:"number"`
}

func main() {
	// 1. Build the request object
	req := Request{
		Name:   "Tibi",
		Number: 42,
	}

	// 2. Marshal it into JSON
	reqBytes, err := json.Marshal(req)
	if err != nil {
		fmt.Printf("Error marshaling request: %v\n", err)
		return
	}

	// 3. Prepare the PowerShell command
	cmd := exec.Command("pwsh", "-File", "json_echo.ps1", "-Operation", "echo")

	// 4. Wire stdin, stdout, stderr
	cmd.Stdin = bytes.NewReader(reqBytes)

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	// 5. Run PowerShell
	err = cmd.Run()
	if err != nil {
		fmt.Printf("Error running PowerShell: %v\n", err)
		if stderr.Len() > 0 {
            fmt.Printf("Stderr: %s\n", stderr.String())
        }
		return
	}

	// 6. Inspect raw output
	raw := stdout.Bytes()
	fmt.Printf("Raw JSON from PowerShell: %s\n", string(raw))

	// 7. Unmarshal JSON into the Response struct
	var resp Response
	err = json.Unmarshal(raw, &resp)
	if err != nil {
		fmt.Printf("Error unmarshaling response: %v\n", err)
		return
	}

	// 8. Use the typed result
	fmt.Println("Parsed response:")
	fmt.Printf("  Message: %s\n", resp.Message)
	fmt.Printf("  Name:    %s\n", resp.Name)
	fmt.Printf("  Number:  %d\n", resp.Number)
}
